"""Append-only anchor persistence with knowledge-cutoff-safe approval lookup."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from src.domain.technical_anchor import ManualAnchorSetRevision, TechnicalAnchorApproval
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class TechnicalAnchorRepository:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _bind(conn, key: str, fingerprint: str, resource_type: str, resource_id: str) -> None:
        conn.execute(
            "INSERT INTO technical_anchor_idempotency_keys VALUES (?,?,?,?,?)",
            (key, fingerprint, resource_type, resource_id, utc_now_timestamp()),
        )

    @staticmethod
    def _existing(
        conn, key: str, fingerprint: str, resource_type: str, table: str, id_column: str
    ) -> dict | None:
        binding = conn.execute(
            "SELECT * FROM technical_anchor_idempotency_keys WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if binding:
            if binding["payload_fingerprint"] != fingerprint or binding["resource_type"] != resource_type:
                raise ValueError("idempotency key was already used with a different payload")
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {id_column} = ?", (binding["resource_id"],)
            ).fetchone()
            if row is None:
                raise RuntimeError("idempotency ledger references a missing resource")
            result = dict(row)
            result["created"] = False
            return result
        duplicate = conn.execute(
            f"SELECT * FROM {table} WHERE payload_fingerprint = ?", (fingerprint,)
        ).fetchone()
        if duplicate:
            resource_id = duplicate[id_column]
            conn.execute(
                "INSERT INTO technical_anchor_idempotency_keys VALUES (?,?,?,?,?)",
                (key, fingerprint, resource_type, resource_id, utc_now_timestamp()),
            )
            result = dict(duplicate)
            result["created"] = False
            return result
        return None

    def add_anchor_revision(
        self,
        revision: ManualAnchorSetRevision,
        idempotency_key: str,
        *,
        ingested_at: str | None = None,
    ) -> dict:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = revision.canonical_payload()
        fingerprint = _fingerprint(payload)
        record_id = f"anchor_{fingerprint[:24]}"
        ingested = normalize_utc_timestamp(ingested_at or utc_now_timestamp(), "ingested_at")
        if ingested < payload["available_at"]:
            raise ValueError("ingested_at cannot precede anchor available_at")
        with self._connect() as conn:
            existing = self._existing(
                conn, idempotency_key, fingerprint, "anchor_revision",
                "technical_anchor_revisions", "id"
            )
            if existing:
                existing["anchors"] = json.loads(existing.pop("anchors_json"))
                return existing
            previous = conn.execute(
                "SELECT * FROM technical_anchor_revisions WHERE logical_anchor_set_id = ? ORDER BY revision_number DESC LIMIT 1",
                (payload["logical_anchor_set_id"],),
            ).fetchone()
            if payload["revision_number"] == 1:
                if previous is not None:
                    raise ValueError("logical anchor set already has a first revision")
            else:
                if previous is None or previous["id"] != payload["revision_of"]:
                    raise ValueError("revision_of must reference the latest anchor revision")
                if payload["revision_number"] != previous["revision_number"] + 1:
                    raise ValueError("anchor revision_number must increment by one")
                for field in ("symbol", "evidence_basis_rule_id", "price_unit"):
                    if payload[field] != previous[field]:
                        raise ValueError(f"anchor revision cannot change {field}")
            conn.execute(
                """
                INSERT INTO technical_anchor_revisions (
                    id,idempotency_key,payload_fingerprint,logical_anchor_set_id,revision_number,
                    revision_of,symbol,evidence_basis_rule_id,anchors_json,price_unit,
                    available_at,ingested_at,created_at,created_by,source,source_note,status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (record_id, idempotency_key, fingerprint, payload["logical_anchor_set_id"],
                 payload["revision_number"], payload["revision_of"], payload["symbol"],
                 payload["evidence_basis_rule_id"], json.dumps(payload["anchors"], sort_keys=True),
                 payload["price_unit"], payload["available_at"], ingested, ingested,
                 payload["created_by"], payload["source"], payload["source_note"], payload["status"]),
            )
            self._bind(conn, idempotency_key, fingerprint, "anchor_revision", record_id)
            result = dict(conn.execute(
                "SELECT * FROM technical_anchor_revisions WHERE id = ?", (record_id,)
            ).fetchone())
            result["anchors"] = json.loads(result.pop("anchors_json"))
            result["created"] = True
            return result

    def add_approval(
        self,
        approval: TechnicalAnchorApproval,
        idempotency_key: str,
        *,
        ingested_at: str | None = None,
    ) -> dict:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = approval.canonical_payload()
        fingerprint = _fingerprint(payload)
        ingested = normalize_utc_timestamp(ingested_at or utc_now_timestamp(), "ingested_at")
        if ingested < payload["approved_at"]:
            raise ValueError("ingested_at cannot precede approval approved_at")
        event_id = f"anchor_approval_event_{fingerprint[:20]}"
        with self._connect() as conn:
            existing = self._existing(
                conn, idempotency_key, fingerprint, "anchor_approval",
                "technical_anchor_approvals", "approval_id"
            )
            if existing:
                return existing
            anchor = conn.execute(
                "SELECT * FROM technical_anchor_revisions WHERE id = ?", (payload["anchor_revision_id"],)
            ).fetchone()
            if anchor is None:
                raise ValueError("anchor revision does not exist")
            if anchor["evidence_basis_rule_id"] != payload["rule_id"]:
                raise ValueError("approval rule_id must match the anchor revision evidence basis")
            if payload["approved_at"] < anchor["available_at"]:
                raise ValueError("approval approved_at cannot precede anchor available_at")
            previous = conn.execute(
                """SELECT approved_at FROM technical_anchor_approvals
                   WHERE anchor_revision_id = ? AND rule_id = ?
                   ORDER BY approved_at DESC, ingested_at DESC LIMIT 1""",
                (payload["anchor_revision_id"], payload["rule_id"]),
            ).fetchone()
            if previous and payload["approved_at"] < previous["approved_at"]:
                raise ValueError("approval events cannot be backdated")
            conn.execute(
                """
                INSERT INTO technical_anchor_approvals (
                    approval_event_id,approval_id,payload_fingerprint,anchor_revision_id,
                    decision,rule_id,rule_version,evidence_level,implementation_mode,
                    project_operationalization,approved_by,rationale,approved_at,ingested_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (event_id, payload["approval_id"], fingerprint, payload["anchor_revision_id"],
                 payload["decision"], payload["rule_id"], payload["rule_version"],
                 payload["evidence_level"], payload["implementation_mode"],
                 int(payload["project_operationalization"]),
                 payload["approved_by"], payload["rationale"], payload["approved_at"], ingested),
            )
            self._bind(conn, idempotency_key, fingerprint, "anchor_approval", payload["approval_id"])
            result = dict(conn.execute(
                "SELECT * FROM technical_anchor_approvals WHERE approval_id = ?", (payload["approval_id"],)
            ).fetchone())
            result["created"] = True
            return result

    def states_as_of(self, symbol: str, knowledge_cutoff_at: str) -> list[dict]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY logical_anchor_set_id
                        ORDER BY revision_number DESC, available_at DESC, ingested_at DESC, id DESC
                    ) AS rank_no
                    FROM technical_anchor_revisions
                    WHERE symbol = ? AND available_at <= ? AND ingested_at <= ?
                )
                SELECT * FROM ranked WHERE rank_no = 1 ORDER BY logical_anchor_set_id
                """,
                (symbol.strip().upper(), cutoff, cutoff),
            ).fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["anchors"] = json.loads(item.pop("anchors_json"))
                decision = conn.execute(
                    """
                    SELECT * FROM technical_anchor_approvals
                    WHERE anchor_revision_id = ? AND rule_id = ?
                      AND approved_at <= ? AND ingested_at <= ?
                    ORDER BY approved_at DESC, ingested_at DESC, approval_event_id DESC
                    LIMIT 1
                    """,
                    (item["id"], item["evidence_basis_rule_id"], cutoff, cutoff),
                ).fetchone()
                item["approval"] = dict(decision) if decision else None
                results.append(item)
        return results
