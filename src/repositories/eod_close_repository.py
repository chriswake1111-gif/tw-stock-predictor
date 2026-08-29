"""Append-only EOD evidence persistence and deterministic read selection."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.domain.data_foundation import canonical_json, sha256_text
from src.domain.eod_close import (
    EOD_ASOF_SELECTION_SCOPE,
    EOD_PRICE_SEMANTICS_VERSION,
)
from src.domain.universe import parse_canonical_symbol, validate_knowledge_cutoff_at
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import UniverseRepository


TWSE_EOD_RESOURCE_ID = "twse.eod.stock_day_all"
TPEX_EOD_RESOURCE_ID = "tpex.eod.daily_close_quotes"
CLASSIFICATION_RESOURCE_ID = "twse.isin.security_classification"


class EodStorageUnavailable(RuntimeError):
    code = "eod_storage_unavailable"


class EodEvidenceConflict(ValueError):
    code = "eod_evidence_conflict"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


class EodCloseRepository:
    """EOD repository with explicit write helpers and query-only read paths."""

    def __init__(self, db_path: str = "data/cache.db", *, auto_migrate: bool = False):
        self.db_path = db_path
        if auto_migrate:
            # Only operator/install tooling may opt into migration. Public reads
            # intentionally never create or alter database state.
            apply_valuation_migration(db_path)
        self._identity_repository = UniverseRepository(db_path)

    def _connect(self, *, query_only: bool = False) -> sqlite3.Connection:
        if query_only and self.db_path != ":memory:" and not Path(self.db_path).is_file():
            raise EodStorageUnavailable("phase14 database is not available")
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
        except sqlite3.Error as exc:
            raise EodStorageUnavailable(str(exc)) from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='eod_close_source_snapshots'"
            ).fetchone()
        except sqlite3.Error as exc:
            conn.close()
            raise EodStorageUnavailable(str(exc)) from exc
        if exists is None:
            conn.close()
            raise EodStorageUnavailable("phase14 migration is not available")
        if query_only:
            conn.execute("PRAGMA query_only = ON")
        return conn

    @contextmanager
    def _connection(
        self, connection: sqlite3.Connection | None = None
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return
        with self._connect() as conn:
            yield conn

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one operator command's evidence writes atomically."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect(query_only=True)
        try:
            conn.execute("BEGIN")
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def resource_id_for_venue(venue: str) -> str:
        normalized = str(venue).strip().upper()
        if normalized == "TWSE":
            return TWSE_EOD_RESOURCE_ID
        if normalized == "TPEX":
            return TPEX_EOD_RESOURCE_ID
        raise ValueError("venue must be TWSE or TPEX")

    @staticmethod
    def _snapshot_order(as_of: bool) -> str:
        if as_of:
            return "available_at DESC, ingested_at DESC, revision_number DESC, source_snapshot_id DESC, normalized_payload_sha256 DESC"
        return "ingested_at DESC, CASE WHEN available_at IS NULL THEN 1 ELSE 0 END, available_at DESC, revision_number DESC, source_snapshot_id DESC, normalized_payload_sha256 DESC"

    @staticmethod
    def _classification_order(as_of: bool) -> str:
        if as_of:
            return "available_at DESC, ingested_at DESC, revision_number DESC, classification_evidence_id DESC, normalized_payload_sha256 DESC"
        return "ingested_at DESC, CASE WHEN available_at IS NULL THEN 1 ELSE 0 END, available_at DESC, revision_number DESC, classification_evidence_id DESC, normalized_payload_sha256 DESC"

    @staticmethod
    def _visible_where(alias: str, *, as_of: bool) -> str:
        # Availability is the authoritative publication boundary for both
        # current and point-in-time reads.  Ingestion alone only says when the
        # repository learned about a row; it must never make future-available
        # evidence visible to an earlier evaluated_at/cutoff.
        return f"{alias}.available_at IS NOT NULL AND {alias}.available_at <= ? AND {alias}.ingested_at IS NOT NULL AND {alias}.ingested_at <= ?"

    def price_policy(self, conn: sqlite3.Connection, resource_id: str) -> dict[str, Any] | None:
        return self._row(conn.execute(
            "SELECT * FROM eod_price_resource_policies WHERE resource_id = ?",
            (resource_id,),
        ).fetchone())

    def price_policy_for_resource(self, resource_id: str) -> dict[str, Any] | None:
        with self._connect(query_only=True) as conn:
            return self.price_policy(conn, resource_id)

    def ingestion_idempotency(
        self,
        idempotency_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self._connection(connection) as conn:
            return self._row(conn.execute(
                "SELECT * FROM eod_ingestion_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone())

    def ingestion_content_idempotency(
        self,
        *,
        resource_id: str,
        payload_fingerprint: str,
        logical_revision_key: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        """Find prior content evidence within the same resource/logical scope."""
        resource = _required(resource_id, "resource_id")
        fingerprint = _required(payload_fingerprint, "payload_fingerprint").lower()
        logical_key = _required(logical_revision_key, "logical_revision_key")
        with self._connection(connection) as conn:
            return self._row(conn.execute(
                """SELECT i.*
                   FROM eod_ingestion_idempotency i
                   JOIN raw_resource_revisions raw
                     ON raw.raw_resource_revision_id = i.raw_resource_revision_id
                   WHERE i.resource_id = ?
                     AND i.payload_fingerprint = ?
                     AND raw.logical_revision_key = ?
                   ORDER BY i.created_at ASC, i.idempotency_key ASC LIMIT 1""",
                (resource, fingerprint, logical_key),
            ).fetchone())

    def source_snapshot_by_id(
        self,
        source_snapshot_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self._connection(connection) as conn:
            return self._row(conn.execute(
                "SELECT * FROM eod_close_source_snapshots WHERE source_snapshot_id = ?",
                (_required(source_snapshot_id, "source_snapshot_id"),),
            ).fetchone())

    def observations_for_source_snapshot(
        self,
        *,
        source_snapshot_id: str,
        raw_resource_revision_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        with self._connection(connection) as conn:
            return [dict(row) for row in conn.execute(
                """SELECT * FROM eod_close_observations
                   WHERE source_snapshot_id = ? AND raw_resource_revision_id = ?
                   ORDER BY official_code, trade_date, revision_number,
                            close_observation_id""",
                (
                    _required(source_snapshot_id, "source_snapshot_id"),
                    _required(raw_resource_revision_id, "raw_resource_revision_id"),
                ),
            ).fetchall()]

    def classification_for_raw_revision(
        self,
        *,
        official_code: str,
        raw_resource_revision_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self._connection(connection) as conn:
            return self._row(conn.execute(
                """SELECT * FROM eod_product_classification_evidence
                   WHERE resource_id = ? AND official_code = ?
                     AND raw_resource_revision_id = ?
                   ORDER BY revision_number DESC,
                            classification_evidence_id DESC LIMIT 1""",
                (
                    CLASSIFICATION_RESOURCE_ID,
                    _required(official_code, "official_code"),
                    _required(raw_resource_revision_id, "raw_resource_revision_id"),
                ),
            ).fetchone())

    @staticmethod
    def _command_row(
        conn: sqlite3.Connection, idempotency_key: str
    ) -> dict[str, Any] | None:
        return EodCloseRepository._row(conn.execute(
            """SELECT r.*, run.status AS run_status,
                      lock_row.lease_expires_at AS lease_expires_at
               FROM eod_ingestion_command_reservations r
               LEFT JOIN ingestion_runs run ON run.ingestion_run_id = r.run_id
               LEFT JOIN ingestion_resource_locks lock_row
                 ON lock_row.resource_id = r.resource_id
                AND lock_row.owner_run_id = r.run_id
               WHERE r.idempotency_key = ?""",
            (idempotency_key,),
        ).fetchone())

    @staticmethod
    def _validate_command_reservation(
        existing: dict[str, Any],
        *,
        payload_fingerprint: str,
        resource_id: str,
        source_published_at: str | None,
    ) -> None:
        if existing["payload_fingerprint"] != payload_fingerprint:
            raise EodEvidenceConflict("idempotency_key_reused")
        if existing["resource_id"] != resource_id:
            raise EodEvidenceConflict("idempotency_key_resource_reused")
        if existing["source_published_at"] != source_published_at:
            raise EodEvidenceConflict("idempotency_key_parameters_reused")

    def ingestion_command_reservation(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self._connect(query_only=True) as conn:
            return self._command_row(conn, idempotency_key)

    def reserve_ingestion_command(
        self,
        *,
        idempotency_key: str,
        payload_fingerprint: str,
        resource_id: str,
        actor_id: str,
        command_received_at: str,
        source_published_at: str | None,
    ) -> dict[str, Any]:
        key = _required(idempotency_key, "idempotency_key")
        fingerprint = _required(payload_fingerprint, "payload_fingerprint").lower()
        resource = _required(resource_id, "resource_id")
        actor = _required(actor_id, "actor_id")
        received = normalize_utc_timestamp(command_received_at, "command_received_at")
        published = (
            normalize_utc_timestamp(source_published_at, "source_published_at")
            if source_published_at
            else None
        )
        with self.write_transaction() as conn:
            existing = self._command_row(conn, key)
            if existing:
                self._validate_command_reservation(
                    existing,
                    payload_fingerprint=fingerprint,
                    resource_id=resource,
                    source_published_at=published,
                )
                return {**existing, "created": False}
            now = utc_now_timestamp()
            conn.execute(
                """INSERT INTO eod_ingestion_command_reservations (
                       idempotency_key, payload_fingerprint, resource_id, actor_id,
                       command_received_at, source_published_at, status, run_id,
                       lock_id, audit_id, result_json, reserved_at, updated_at,
                       last_error
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key, fingerprint, resource, actor, received, published,
                    "reserved", None, None, None, None, received, now, None,
                ),
            )
            row = self._command_row(conn, key)
            if row is None:
                raise RuntimeError("EOD ingestion command reservation was not stored")
            return {**row, "created": True}

    def claim_ingestion_command(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        lock_id: str,
        audit_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        key = _required(idempotency_key, "idempotency_key")
        run = _required(run_id, "run_id")
        lock = _required(lock_id, "lock_id")
        audit = _required(audit_id, "audit_id")
        with self._connection(connection) as conn:
            changed = conn.execute(
                """UPDATE eod_ingestion_command_reservations
                   SET status='running', run_id=?, lock_id=?, audit_id=?,
                       updated_at=?, last_error=NULL
                   WHERE idempotency_key=? AND status='reserved'""",
                (run, lock, audit, utc_now_timestamp(), key),
            ).rowcount
            return self._command_row(conn, key) if changed == 1 else None

    def complete_ingestion_command(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        result_json: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        key = _required(idempotency_key, "idempotency_key")
        run = _required(run_id, "run_id")
        result = _required(result_json, "result_json")
        with self._connection(connection) as conn:
            changed = conn.execute(
                """UPDATE eod_ingestion_command_reservations
                   SET status='completed', result_json=?, updated_at=?
                   WHERE idempotency_key=? AND run_id=? AND status='running'""",
                (result, utc_now_timestamp(), key, run),
            ).rowcount
            if changed != 1:
                raise EodEvidenceConflict("idempotency_command_state_conflict")
            return self._command_row(conn, key) or {}

    def reset_ingestion_command(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        error_code: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        key = _required(idempotency_key, "idempotency_key")
        run = _required(run_id, "run_id")
        reason = _required(error_code, "error_code")[:160]
        with self._connection(connection) as conn:
            changed = conn.execute(
                """UPDATE eod_ingestion_command_reservations
                   SET status='reserved', updated_at=?, last_error=?
                   WHERE idempotency_key=? AND run_id=? AND status='running'""",
                (utc_now_timestamp(), reason, key, run),
            ).rowcount
            if changed != 1:
                raise EodEvidenceConflict("idempotency_command_state_conflict")
            return self._command_row(conn, key) or {}

    def latest_source_snapshot_for_logical(
        self,
        *,
        resource_id: str,
        logical_revision_key: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self._connection(connection) as conn:
            return self._row(conn.execute(
                """SELECT * FROM eod_close_source_snapshots
                   WHERE resource_id = ? AND logical_revision_key = ?
                   ORDER BY revision_number DESC, ingested_at DESC,
                            source_snapshot_id DESC LIMIT 1""",
                (resource_id, logical_revision_key),
            ).fetchone())

    def latest_classification_for_code(
        self,
        official_code: str,
        *,
        market_raw: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self._connection(connection) as conn:
            clauses = [
                "resource_id = ?",
                "official_code = ?",
            ]
            params: list[Any] = [CLASSIFICATION_RESOURCE_ID, official_code]
            if market_raw is not None:
                clauses.append("market_raw = ?")
                params.append(market_raw)
            return self._row(conn.execute(
                f"""SELECT * FROM eod_product_classification_evidence
                   WHERE {' AND '.join(clauses)}
                   ORDER BY revision_number DESC, ingested_at DESC,
                            classification_evidence_id DESC LIMIT 1""",
                params,
            ).fetchone())

    def latest_observation(
        self,
        *,
        resource_id: str,
        official_code: str,
        trade_date: str | None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self._connection(connection) as conn:
            return self._row(conn.execute(
                """SELECT * FROM eod_close_observations
                   WHERE resource_id = ? AND official_code = ?
                     AND trade_date IS ?
                   ORDER BY revision_number DESC, ingested_at DESC,
                            close_observation_id DESC LIMIT 1""",
                (resource_id, official_code, trade_date),
            ).fetchone())

    def latest_source_snapshot(
        self,
        conn: sqlite3.Connection,
        *,
        resource_id: str,
        as_of: bool = False,
        cutoff: str | None = None,
        source_trade_date: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["resource_id = ?"]
        params: list[Any] = [resource_id]
        if as_of:
            if not cutoff:
                raise ValueError("cutoff is required for as-of source selection")
            clauses.append(self._visible_where("s", as_of=True))
            params.extend([cutoff, cutoff])
        elif cutoff:
            clauses.append(self._visible_where("s", as_of=False))
            params.extend([cutoff, cutoff])
        if source_trade_date is not None:
            clauses.extend(["source_trade_date = ?", "source_trade_date_status = 'valid'"])
            params.append(source_trade_date)
        return self._row(conn.execute(
            f"""SELECT s.* FROM eod_close_source_snapshots s
                WHERE {' AND '.join(clauses)}
                ORDER BY {self._snapshot_order(as_of)} LIMIT 1""",
            params,
        ).fetchone())

    def latest_classification(
        self,
        conn: sqlite3.Connection,
        *,
        official_code: str,
        market_raw: str | None = None,
        as_of: bool = False,
        cutoff: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = [
            "resource_id = ?",
            "official_code = ?",
        ]
        params: list[Any] = [CLASSIFICATION_RESOURCE_ID, official_code]
        if market_raw is not None:
            clauses.append("market_raw = ?")
            params.append(market_raw)
        if as_of:
            if not cutoff:
                raise ValueError("cutoff is required for as-of classification selection")
            clauses.append(self._visible_where("c", as_of=True))
            params.extend([cutoff, cutoff])
        elif cutoff:
            clauses.append(self._visible_where("c", as_of=False))
            params.extend([cutoff, cutoff])
        return self._row(conn.execute(
            f"""SELECT c.* FROM eod_product_classification_evidence c
                WHERE {' AND '.join(clauses)}
                ORDER BY {self._classification_order(as_of)} LIMIT 1""",
            params,
        ).fetchone())

    def observation_for_snapshot(
        self,
        conn: sqlite3.Connection,
        *,
        source_snapshot_id: str,
        official_code: str,
        as_of: bool = False,
        cutoff: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["o.source_snapshot_id = ?", "o.official_code = ?"]
        params: list[Any] = [source_snapshot_id, official_code]
        if as_of:
            if not cutoff:
                raise ValueError("cutoff is required for as-of observation selection")
            clauses.append(self._visible_where("o", as_of=True))
            params.extend([cutoff, cutoff])
        elif cutoff:
            clauses.append(self._visible_where("o", as_of=False))
            params.extend([cutoff, cutoff])
        return self._row(conn.execute(
            f"""SELECT o.* FROM eod_close_observations o
               WHERE {' AND '.join(clauses)}
               ORDER BY o.revision_number DESC, o.close_observation_id DESC,
                        o.normalized_payload_sha256 DESC LIMIT 1""",
            params,
        ).fetchone())

    def identity_for_eod(
        self,
        conn: sqlite3.Connection,
        *,
        canonical_symbol: str,
        cutoff: str,
    ) -> dict[str, Any] | None:
        return self._identity_repository.identity_context_for_eod(
            canonical_symbol,
            knowledge_cutoff_at=cutoff,
            conn=conn,
        )

    def current_bundle(
        self,
        conn: sqlite3.Connection,
        *,
        canonical_symbol: str,
        evaluated_at: str,
    ) -> dict[str, Any]:
        venue, code = parse_canonical_symbol(canonical_symbol)
        resource_id = self.resource_id_for_venue(venue.value)
        snapshot = self.latest_source_snapshot(
            conn, resource_id=resource_id, cutoff=evaluated_at
        )
        return {
            "venue": venue.value,
            "official_code": code,
            "resource_id": resource_id,
            "resource": self._row(conn.execute(
                "SELECT * FROM data_resources WHERE resource_id = ?", (resource_id,)
            ).fetchone()),
            "policy": self.price_policy(conn, resource_id),
            "snapshot": snapshot,
            "observation": self.observation_for_snapshot(
                conn, source_snapshot_id=snapshot["source_snapshot_id"], official_code=code,
                cutoff=evaluated_at,
            ) if snapshot else None,
            "classification": self.latest_classification(
                conn,
                official_code=code,
                market_raw="上市" if venue.value == "TWSE" else "上櫃",
                cutoff=evaluated_at,
            ),
            "identity": self.identity_for_eod(
                conn, canonical_symbol=canonical_symbol, cutoff=evaluated_at
            ),
            "evaluated_at": evaluated_at,
            "knowledge_cutoff_at": None,
            "selection_scope": None,
        }

    def as_of_bundle(
        self,
        conn: sqlite3.Connection,
        *,
        canonical_symbol: str,
        knowledge_cutoff_at: str,
    ) -> dict[str, Any]:
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        venue, code = parse_canonical_symbol(canonical_symbol)
        resource_id = self.resource_id_for_venue(venue.value)
        date_row = conn.execute(
            f"""SELECT source_trade_date
                FROM eod_close_source_snapshots s
                WHERE s.resource_id = ?
                  AND s.source_trade_date_status = 'valid'
                  AND s.source_trade_date IS NOT NULL
                  AND {self._visible_where('s', as_of=True)}
                ORDER BY source_trade_date DESC LIMIT 1""",
            (resource_id, cutoff, cutoff),
        ).fetchone()
        selected_date = str(date_row[0]) if date_row else None
        snapshot = self.latest_source_snapshot(
            conn,
            resource_id=resource_id,
            as_of=True,
            cutoff=cutoff,
            source_trade_date=selected_date,
        ) if selected_date else self.latest_source_snapshot(
            conn, resource_id=resource_id, as_of=True, cutoff=cutoff
        )
        return {
            "venue": venue.value,
            "official_code": code,
            "resource_id": resource_id,
            "resource": self._row(conn.execute(
                "SELECT * FROM data_resources WHERE resource_id = ?", (resource_id,)
            ).fetchone()),
            "policy": self.price_policy(conn, resource_id),
            "snapshot": snapshot,
            "observation": self.observation_for_snapshot(
                conn, source_snapshot_id=snapshot["source_snapshot_id"], official_code=code,
                as_of=True, cutoff=cutoff,
            ) if snapshot else None,
            "classification": self.latest_classification(
                conn,
                official_code=code,
                market_raw="上市" if venue.value == "TWSE" else "上櫃",
                as_of=True,
                cutoff=cutoff,
            ),
            "identity": self.identity_for_eod(
                conn, canonical_symbol=canonical_symbol, cutoff=cutoff
            ),
            "evaluated_at": cutoff,
            "knowledge_cutoff_at": cutoff,
            "selection_scope": EOD_ASOF_SELECTION_SCOPE,
            "selected_trade_date": selected_date,
        }

    # ----- Operator-only append paths -----

    def add_source_snapshot(
        self,
        payload: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        resource_id = _required(payload.get("resource_id"), "resource_id")
        if resource_id not in {TWSE_EOD_RESOURCE_ID, TPEX_EOD_RESOURCE_ID}:
            raise ValueError("resource_id is not an approved EOD close resource")
        logical_key = _required(payload.get("logical_revision_key"), "logical_revision_key")
        raw_hash = _required(payload.get("raw_payload_sha256"), "raw_payload_sha256").lower()
        identity = payload.get("identity_fingerprint") or sha256_text(canonical_json({
            "resource_id": resource_id,
            "logical_revision_key": logical_key,
            "raw_payload_sha256": raw_hash,
            "status": payload.get("status"),
        }))
        with self._connection(connection) as conn:
            raw_revision = conn.execute(
                "SELECT resource_id, raw_payload_sha256 FROM raw_resource_revisions WHERE raw_resource_revision_id = ?",
                (_required(payload.get("raw_resource_revision_id"), "raw_resource_revision_id"),),
            ).fetchone()
            if (
                raw_revision is None
                or raw_revision["resource_id"] != resource_id
                or str(raw_revision["raw_payload_sha256"]).lower() != raw_hash
            ):
                raise ValueError("source snapshot raw revision mismatch")
            existing = conn.execute(
                "SELECT * FROM eod_close_source_snapshots WHERE identity_fingerprint = ? OR (resource_id = ? AND logical_revision_key = ? AND raw_payload_sha256 = ?)",
                (identity, resource_id, logical_key, raw_hash),
            ).fetchone()
            if existing:
                return {**dict(existing), "created": False}
            previous = conn.execute(
                """SELECT * FROM eod_close_source_snapshots
                   WHERE resource_id = ? AND logical_revision_key = ?
                   ORDER BY revision_number DESC, ingested_at DESC,
                            source_snapshot_id DESC LIMIT 1""",
                (resource_id, logical_key),
            ).fetchone()
            supersedes = payload.get("supersedes_source_snapshot_id")
            if previous and supersedes != previous["source_snapshot_id"]:
                raise EodEvidenceConflict("corrected source snapshot must supersede latest revision")
            if previous is None and supersedes is not None:
                raise EodEvidenceConflict("first source snapshot cannot supersede another revision")
            revision_number = int(payload.get("revision_number") or (int(previous["revision_number"]) + 1 if previous else 1))
            snapshot_id = str(payload.get("source_snapshot_id") or f"eodsrc_{identity[:24]}")
            fields = {
                "source_snapshot_id": snapshot_id,
                "resource_id": resource_id,
                "raw_resource_revision_id": _required(payload.get("raw_resource_revision_id"), "raw_resource_revision_id"),
                "logical_revision_key": logical_key,
                "revision_number": revision_number,
                "source_trade_date": payload.get("source_trade_date"),
                "source_trade_date_status": str(payload.get("source_trade_date_status") or ("valid" if payload.get("source_trade_date") else "missing")),
                "status": str(payload.get("status") or "available"),
                "coverage_state": str(payload.get("coverage_state") or "complete"),
                "coverage_proof_type": payload.get("coverage_proof_type"),
                "coverage_proof_reference": payload.get("coverage_proof_reference"),
                "row_count": int(payload.get("row_count") or 0),
                "source_date_min": payload.get("source_date_min"),
                "source_date_max": payload.get("source_date_max"),
                "source_published_at": payload.get("source_published_at"),
                "fetched_at": _required(payload.get("fetched_at"), "fetched_at"),
                "received_at": _required(payload.get("received_at"), "received_at"),
                "available_at": payload.get("available_at"),
                "ingested_at": _required(payload.get("ingested_at"), "ingested_at"),
                "source_url": _required(payload.get("source_url"), "source_url"),
                "http_method": str(payload.get("http_method") or "GET"),
                "response_format": str(payload.get("response_format") or "json"),
                "contract_version": _required(payload.get("contract_version"), "contract_version"),
                "parser_version": _required(payload.get("parser_version"), "parser_version"),
                "schema_fingerprint": _required(payload.get("schema_fingerprint"), "schema_fingerprint"),
                "raw_payload_sha256": raw_hash,
                "normalized_payload_sha256": _required(payload.get("normalized_payload_sha256"), "normalized_payload_sha256"),
                "query_dimensions_json": payload.get("query_dimensions_json") or _json({}),
                "source_record_reference": _required(payload.get("source_record_reference"), "source_record_reference"),
                "source_scope": _required(payload.get("source_scope"), "source_scope"),
                "reason": payload.get("reason"),
                "supersedes_source_snapshot_id": supersedes,
                "revocation_reference": payload.get("revocation_reference"),
                "identity_fingerprint": identity,
            }
            if fields["coverage_state"] == "partial" and not (fields["coverage_proof_type"] and fields["coverage_proof_reference"]):
                raise ValueError("partial source snapshot requires positive coverage proof")
            columns = ",".join(fields)
            placeholders = ",".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO eod_close_source_snapshots ({columns}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
            return {**dict(conn.execute(
                "SELECT * FROM eod_close_source_snapshots WHERE source_snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()), "created": True}

    def add_classification_evidence(
        self,
        payload: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        resource_id = _required(payload.get("resource_id") or CLASSIFICATION_RESOURCE_ID, "resource_id")
        if resource_id != CLASSIFICATION_RESOURCE_ID:
            raise ValueError("resource_id is not the approved classification resource")
        official_code = _required(payload.get("official_code"), "official_code")
        raw_hash = _required(payload.get("raw_payload_sha256"), "raw_payload_sha256").lower()
        identity = payload.get("identity_fingerprint") or sha256_text(canonical_json({
            "resource_id": resource_id,
            "official_code": official_code,
            "raw_payload_sha256": raw_hash,
            "classification_state": payload.get("classification_state"),
        }))
        with self._connection(connection) as conn:
            raw_revision = conn.execute(
                "SELECT resource_id, raw_payload_sha256 FROM raw_resource_revisions WHERE raw_resource_revision_id = ?",
                (_required(payload.get("raw_resource_revision_id"), "raw_resource_revision_id"),),
            ).fetchone()
            if (
                raw_revision is None
                or raw_revision["resource_id"] != resource_id
                or str(raw_revision["raw_payload_sha256"]).lower() != raw_hash
            ):
                raise ValueError("classification raw revision mismatch")
            existing = conn.execute(
                "SELECT * FROM eod_product_classification_evidence WHERE identity_fingerprint = ? OR (resource_id = ? AND official_code = ? AND raw_payload_sha256 = ?)",
                (identity, resource_id, official_code, raw_hash),
            ).fetchone()
            if existing:
                return {**dict(existing), "created": False}
            market_raw = payload.get("market_raw")
            market_clause = "market_raw = ?" if market_raw is not None else "market_raw IS NULL"
            market_params = [market_raw] if market_raw is not None else []
            previous = conn.execute(
                f"""SELECT * FROM eod_product_classification_evidence
                   WHERE resource_id = ? AND official_code = ?
                     AND {market_clause}
                   ORDER BY revision_number DESC, ingested_at DESC,
                            classification_evidence_id DESC LIMIT 1""",
                [resource_id, official_code, *market_params],
            ).fetchone()
            supersedes = payload.get("supersedes_classification_evidence_id")
            if previous and supersedes != previous["classification_evidence_id"]:
                raise EodEvidenceConflict("corrected classifier evidence must supersede latest revision")
            if previous is None and supersedes is not None:
                raise EodEvidenceConflict("first classifier evidence cannot supersede another revision")
            state = str(payload.get("classification_state") or "accepted")
            if state != "accepted" and not str(payload.get("reason") or "").strip():
                raise ValueError("blocking classifier evidence requires a reason")
            fields = {
                "classification_evidence_id": str(payload.get("classification_evidence_id") or f"eodclass_{identity[:24]}"),
                "resource_id": resource_id,
                "raw_resource_revision_id": _required(payload.get("raw_resource_revision_id"), "raw_resource_revision_id"),
                "logical_revision_key": str(payload.get("logical_revision_key") or f"{resource_id}:{official_code}"),
                "official_code": official_code,
                "venue_raw": payload.get("venue_raw"),
                "market_raw": payload.get("market_raw"),
                "security_type_raw": payload.get("security_type_raw"),
                "isin_raw": payload.get("isin_raw"),
                "listing_date": payload.get("listing_date"),
                "cfi_raw": payload.get("cfi_raw"),
                "currency_raw": payload.get("currency_raw"),
                "remarks_raw": payload.get("remarks_raw"),
                "raw_cells_json": payload.get("raw_cells_json") or _json([]),
                "classification_decision": str(payload.get("classification_decision") or "needs_human_input"),
                "classification_state": state,
                "reason": payload.get("reason"),
                "contract_version": str(payload.get("contract_version") or "eod_product_scope_v1"),
                "revision_number": int(payload.get("revision_number") or (int(previous["revision_number"]) + 1 if previous else 1)),
                "supersedes_classification_evidence_id": supersedes,
                "revocation_reference": payload.get("revocation_reference"),
                "source_published_at": payload.get("source_published_at"),
                "fetched_at": _required(payload.get("fetched_at"), "fetched_at"),
                "received_at": _required(payload.get("received_at"), "received_at"),
                "available_at": payload.get("available_at"),
                "ingested_at": _required(payload.get("ingested_at"), "ingested_at"),
                "source_url": _required(payload.get("source_url"), "source_url"),
                "parser_version": _required(payload.get("parser_version"), "parser_version"),
                "schema_fingerprint": _required(payload.get("schema_fingerprint"), "schema_fingerprint"),
                "raw_payload_sha256": raw_hash,
                "normalized_payload_sha256": _required(payload.get("normalized_payload_sha256"), "normalized_payload_sha256"),
                "source_record_reference": _required(payload.get("source_record_reference"), "source_record_reference"),
                "identity_fingerprint": identity,
            }
            columns = ",".join(fields)
            placeholders = ",".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO eod_product_classification_evidence ({columns}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
            return {**dict(conn.execute(
                "SELECT * FROM eod_product_classification_evidence WHERE classification_evidence_id = ?",
                (fields["classification_evidence_id"],),
            ).fetchone()), "created": True}

    def add_observation(
        self,
        payload: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        resource_id = _required(payload.get("resource_id"), "resource_id")
        official_code = _required(payload.get("official_code"), "official_code")
        raw_hash = _required(payload.get("raw_payload_sha256"), "raw_payload_sha256").lower()
        identity = payload.get("identity_fingerprint") or sha256_text(canonical_json({
            "resource_id": resource_id,
            "official_code": official_code,
            "trade_date": payload.get("trade_date"),
            "revision_number": payload.get("revision_number"),
            "raw_payload_sha256": raw_hash,
        }))
        with self._connection(connection) as conn:
            parent = conn.execute(
                "SELECT * FROM eod_close_source_snapshots WHERE source_snapshot_id = ?",
                (_required(payload.get("source_snapshot_id"), "source_snapshot_id"),),
            ).fetchone()
            if parent is None or parent["resource_id"] != resource_id:
                raise ValueError("observation source snapshot mismatch")
            raw_revision = conn.execute(
                "SELECT resource_id, raw_payload_sha256 FROM raw_resource_revisions WHERE raw_resource_revision_id = ?",
                (_required(payload.get("raw_resource_revision_id"), "raw_resource_revision_id"),),
            ).fetchone()
            if (
                raw_revision is None
                or raw_revision["resource_id"] != resource_id
                or str(raw_revision["raw_payload_sha256"]).lower() != raw_hash
            ):
                raise ValueError("observation raw revision mismatch")
            classification_id = payload.get("classification_evidence_id")
            if classification_id:
                classification = conn.execute(
                    "SELECT * FROM eod_product_classification_evidence WHERE classification_evidence_id = ?",
                    (classification_id,),
                ).fetchone()
                if classification is None or classification["official_code"] != official_code:
                    raise ValueError("observation classifier mismatch")
            instrument_revision_id = payload.get("instrument_revision_id")
            instrument_id = payload.get("instrument_id")
            if instrument_revision_id:
                identity_row = conn.execute(
                    """SELECT instrument_id, venue, official_code
                       FROM universe_instrument_revisions
                       WHERE instrument_revision_id = ?""",
                    (instrument_revision_id,),
                ).fetchone()
                if identity_row is None or identity_row["venue"] != payload.get("venue") or identity_row["official_code"] != official_code:
                    raise ValueError("observation identity mismatch")
                if instrument_id and instrument_id != identity_row["instrument_id"]:
                    raise ValueError("observation instrument mismatch")
                instrument_id = identity_row["instrument_id"]
            existing = conn.execute(
                "SELECT * FROM eod_close_observations WHERE identity_fingerprint = ? OR (resource_id = ? AND official_code = ? AND trade_date IS ? AND revision_number = ?)",
                (identity, resource_id, official_code, payload.get("trade_date"), int(payload.get("revision_number") or 1)),
            ).fetchone()
            if existing:
                return {**dict(existing), "created": False}
            previous = conn.execute(
                """SELECT * FROM eod_close_observations
                   WHERE resource_id = ? AND official_code = ? AND trade_date IS ?
                   ORDER BY revision_number DESC, ingested_at DESC,
                            close_observation_id DESC LIMIT 1""",
                (resource_id, official_code, payload.get("trade_date")),
            ).fetchone()
            supersedes = payload.get("supersedes_observation_id")
            if previous and supersedes != previous["close_observation_id"]:
                raise EodEvidenceConflict("corrected observation must supersede latest revision")
            if previous is None and supersedes is not None:
                raise EodEvidenceConflict("first observation cannot supersede another revision")
            fields = {
                "close_observation_id": str(payload.get("close_observation_id") or f"eodobs_{identity[:24]}"),
                "resource_id": resource_id,
                "raw_resource_revision_id": _required(payload.get("raw_resource_revision_id"), "raw_resource_revision_id"),
                "source_snapshot_id": parent["source_snapshot_id"],
                "classification_evidence_id": classification_id,
                "instrument_id": instrument_id,
                "instrument_revision_id": instrument_revision_id,
                "venue": _required(payload.get("venue"), "venue").upper(),
                "official_code": official_code,
                "trade_date": payload.get("trade_date"),
                "trade_date_status": str(payload.get("trade_date_status") or ("valid" if payload.get("trade_date") else "missing")),
                "revision_number": int(payload.get("revision_number") or (int(previous["revision_number"]) + 1 if previous else 1)),
                "supersedes_observation_id": supersedes,
                "raw_close_text": payload.get("raw_close_text"),
                "close_value": payload.get("close_value"),
                "raw_volume_text": payload.get("raw_volume_text"),
                "volume_value": payload.get("volume_value"),
                "raw_trade_indication_text": payload.get("raw_trade_indication_text"),
                "trade_indication_value": payload.get("trade_indication_value"),
                "currency": payload.get("currency"),
                "unit": payload.get("unit"),
                "price_semantics_version": payload.get("price_semantics_version") or EOD_PRICE_SEMANTICS_VERSION,
                "product_scope": str(payload.get("product_scope") or "needs_human_input"),
                "observation_status": str(payload.get("observation_status") or "insufficient_data"),
                "source_observation_state": str(
                    payload.get("source_observation_state") or "source_observed"
                ),
                "public_eligibility_status": str(payload.get("public_eligibility_status") or "ineligible"),
                "quality_status": str(payload.get("quality_status") or "unknown"),
                "quality_flags_json": payload.get("quality_flags_json") or _json([]),
                "row_fingerprint": _required(payload.get("row_fingerprint"), "row_fingerprint"),
                "raw_payload_sha256": raw_hash,
                "normalized_payload_sha256": _required(payload.get("normalized_payload_sha256"), "normalized_payload_sha256"),
                "source_trading_scope": _required(payload.get("source_trading_scope"), "source_trading_scope"),
                "available_at": payload.get("available_at"),
                "ingested_at": _required(payload.get("ingested_at"), "ingested_at"),
                "source_record_reference": _required(payload.get("source_record_reference"), "source_record_reference"),
                "source_note": payload.get("source_note"),
                "identity_fingerprint": identity,
            }
            if fields["source_observation_state"] != "source_observed":
                raise ValueError("source observation state must be source_observed")
            columns = ",".join(fields)
            placeholders = ",".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO eod_close_observations ({columns}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
            return {**dict(conn.execute(
                "SELECT * FROM eod_close_observations WHERE close_observation_id = ?",
                (fields["close_observation_id"],),
            ).fetchone()), "created": True}

    def add_ingestion_idempotency(
        self,
        payload: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        fields = {
            "idempotency_key": _required(payload.get("idempotency_key"), "idempotency_key"),
            "payload_fingerprint": _required(payload.get("payload_fingerprint"), "payload_fingerprint").lower(),
            "resource_id": _required(payload.get("resource_id"), "resource_id"),
            "raw_resource_revision_id": _required(payload.get("raw_resource_revision_id"), "raw_resource_revision_id"),
            "source_snapshot_id": payload.get("source_snapshot_id"),
            "actor_id": _required(payload.get("actor_id"), "actor_id"),
            "created_at": _required(payload.get("created_at"), "created_at"),
        }
        with self._connection(connection) as conn:
            raw_revision = conn.execute(
                "SELECT resource_id, raw_payload_sha256 FROM raw_resource_revisions WHERE raw_resource_revision_id = ?",
                (fields["raw_resource_revision_id"],),
            ).fetchone()
            if (
                raw_revision is None
                or raw_revision["resource_id"] != fields["resource_id"]
                or str(raw_revision["raw_payload_sha256"]).lower()
                != fields["payload_fingerprint"].lower()
            ):
                raise ValueError("idempotency raw revision mismatch")
            if fields["source_snapshot_id"]:
                source = conn.execute(
                    "SELECT resource_id, raw_resource_revision_id FROM eod_close_source_snapshots WHERE source_snapshot_id = ?",
                    (fields["source_snapshot_id"],),
                ).fetchone()
                if (
                    source is None
                    or source["resource_id"] != fields["resource_id"]
                    or source["raw_resource_revision_id"]
                    != fields["raw_resource_revision_id"]
                ):
                    raise ValueError("idempotency source snapshot mismatch")
            existing = conn.execute(
                "SELECT * FROM eod_ingestion_idempotency WHERE idempotency_key = ?",
                (fields["idempotency_key"],),
            ).fetchone()
            if existing:
                if existing["payload_fingerprint"] != fields["payload_fingerprint"]:
                    raise EodEvidenceConflict("idempotency_key_reused")
                return {**dict(existing), "created": False}
            same = conn.execute(
                """SELECT * FROM eod_ingestion_idempotency
                   WHERE resource_id = ?
                     AND raw_resource_revision_id = ?
                     AND payload_fingerprint = ?
                   ORDER BY created_at ASC, idempotency_key ASC LIMIT 1""",
                (
                    fields["resource_id"],
                    fields["raw_resource_revision_id"],
                    fields["payload_fingerprint"],
                ),
            ).fetchone()
            if same:
                if same["source_snapshot_id"] != fields["source_snapshot_id"]:
                    raise EodEvidenceConflict("content_idempotency_scope_conflict")
                return {**dict(same), "created": False}
            columns = ",".join(fields)
            placeholders = ",".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO eod_ingestion_idempotency ({columns}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
            return {**dict(conn.execute(
                "SELECT * FROM eod_ingestion_idempotency WHERE idempotency_key = ?",
                (fields["idempotency_key"],),
            ).fetchone()), "created": True}


__all__ = [
    "CLASSIFICATION_RESOURCE_ID",
    "EodCloseRepository",
    "EodEvidenceConflict",
    "EodStorageUnavailable",
    "TPEX_EOD_RESOURCE_ID",
    "TWSE_EOD_RESOURCE_ID",
]
