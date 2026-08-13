"""Transactional persistence for the Phase 12 research-review workflow."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from src.domain.research_workflow import (
    MembershipState,
    ReviewAcknowledgment,
    WORKFLOW_CONTRACT_VERSION,
    canonical_research_symbol,
)
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


class ResearchWorkflowNotFoundError(LookupError):
    pass


class ResearchWorkflowRepository:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _activate_membership(
        conn: sqlite3.Connection, row: sqlite3.Row, changed_at: str
    ) -> sqlite3.Row:
        if row["membership_state"] == MembershipState.ARCHIVED.value:
            conn.execute(
                """
                UPDATE research_watchlist_items
                SET membership_state='active', archived_at=NULL, updated_at=?
                WHERE watchlist_item_id=?
                """,
                (changed_at, row["watchlist_item_id"]),
            )
            row = conn.execute(
                "SELECT * FROM research_watchlist_items WHERE watchlist_item_id=?",
                (row["watchlist_item_id"],),
            ).fetchone()
            assert row is not None
        return row

    def add_membership(self, symbol: str) -> dict[str, Any]:
        canonical = canonical_research_symbol(symbol)
        changed_at = utc_now_timestamp()
        item_id = "research_watchlist_" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()[:24]
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM research_watchlist_items WHERE symbol=?", (canonical,)
            ).fetchone()
            created = row is None
            restored = bool(row and row["membership_state"] == "archived")
            if row is None:
                conn.execute(
                    "INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)",
                    (item_id, canonical, "active", changed_at, changed_at, None,
                     WORKFLOW_CONTRACT_VERSION),
                )
                row = conn.execute(
                    "SELECT * FROM research_watchlist_items WHERE watchlist_item_id=?",
                    (item_id,),
                ).fetchone()
            else:
                row = self._activate_membership(conn, row, changed_at)
            conn.commit()
            assert row is not None
            return {**self._item(row), "created": created, "restored": restored}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _set_membership_state(self, item_id: str, state: MembershipState) -> dict[str, Any]:
        changed_at = utc_now_timestamp()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM research_watchlist_items WHERE watchlist_item_id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ResearchWorkflowNotFoundError(item_id)
            if state is MembershipState.ACTIVE:
                row = self._activate_membership(conn, row, changed_at)
            elif row["membership_state"] != "archived":
                conn.execute(
                    """
                    UPDATE research_watchlist_items
                    SET membership_state='archived', archived_at=?, updated_at=?
                    WHERE watchlist_item_id=?
                    """,
                    (changed_at, changed_at, item_id),
                )
                row = conn.execute(
                    "SELECT * FROM research_watchlist_items WHERE watchlist_item_id=?",
                    (item_id,),
                ).fetchone()
            conn.commit()
            assert row is not None
            return self._item(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def archive(self, item_id: str) -> dict[str, Any]:
        return self._set_membership_state(item_id, MembershipState.ARCHIVED)

    def unarchive(self, item_id: str) -> dict[str, Any]:
        return self._set_membership_state(item_id, MembershipState.ACTIVE)

    @staticmethod
    def list_memberships_with_connection(
        conn: sqlite3.Connection, *, include_archived: bool = False, limit: int = 25
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 50:
            raise ValueError("research_queue_limit_invalid")
        where = "" if include_archived else "WHERE membership_state='active'"
        rows = conn.execute(
            f"""
            SELECT * FROM research_watchlist_items {where}
            ORDER BY symbol ASC, watchlist_item_id ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def latest_review_events_with_connection(
        conn: sqlite3.Connection, item_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY watchlist_item_id
                    ORDER BY reviewed_at DESC, created_at DESC, review_event_id DESC
                ) AS position
                FROM research_review_events
                WHERE watchlist_item_id IN ({placeholders})
            )
            SELECT * FROM ranked WHERE position=1
            """,
            item_ids,
        ).fetchall()
        return {row["watchlist_item_id"]: dict(row) for row in rows}

    def append_review_event(
        self, acknowledgment: ReviewAcknowledgment, *, reviewed_at: str
    ) -> dict[str, Any]:
        payload = acknowledgment.canonical_payload()
        reviewed = normalize_utc_timestamp(reviewed_at, "reviewed_at")
        event_id = "research_review_" + hashlib.sha256(
            payload["idempotency_key"].encode("utf-8")
        ).hexdigest()[:24]
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM research_review_events WHERE idempotency_key=?",
                (payload["idempotency_key"],),
            ).fetchone()
            if existing:
                same = all(existing[field] == payload[field] for field in (
                    "watchlist_item_id", "acknowledged_snapshot_id",
                    "comparison_cutoff_at",
                ))
                if not same:
                    raise ValueError("review_idempotency_conflict")
                conn.commit()
                return {**dict(existing), "created": False}
            item = conn.execute(
                "SELECT * FROM research_watchlist_items WHERE watchlist_item_id=?",
                (payload["watchlist_item_id"],),
            ).fetchone()
            if item is None:
                raise ResearchWorkflowNotFoundError(payload["watchlist_item_id"])
            snapshot = conn.execute(
                "SELECT symbol FROM analysis_snapshots WHERE snapshot_id=?",
                (payload["acknowledged_snapshot_id"],),
            ).fetchone()
            if snapshot is None:
                raise ValueError("acknowledged_snapshot_not_found")
            if snapshot["symbol"] != item["symbol"]:
                raise ValueError("acknowledged_snapshot_symbol_mismatch")
            conn.execute(
                "INSERT INTO research_review_events VALUES (?,?,?,?,?,?,?,?)",
                (event_id, payload["watchlist_item_id"],
                 payload["acknowledged_snapshot_id"], payload["comparison_cutoff_at"],
                 reviewed, reviewed, payload["idempotency_key"],
                 WORKFLOW_CONTRACT_VERSION),
            )
            row = conn.execute(
                "SELECT * FROM research_review_events WHERE review_event_id=?",
                (event_id,),
            ).fetchone()
            conn.commit()
            assert row is not None
            return {**dict(row), "created": True}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
