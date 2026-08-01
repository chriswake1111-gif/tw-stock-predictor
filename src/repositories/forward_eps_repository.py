"""Immutable, as-of-safe persistence for Forward EPS and PE scenarios."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from src.domain.valuation import (
    ForwardEPSObservation,
    PEScenario,
    PEScope,
    normalize_utc_timestamp,
    utc_now_timestamp,
)
from src.repositories.migration_runner import apply_valuation_migration


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class ForwardEPSRepository:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _idempotent_existing(
        self, conn: sqlite3.Connection, table: str, idempotency_key: str, fingerprint: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE payload_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if row is None:
                return None
            result = _row_dict(row)
            result["created"] = False
            return result
        result = _row_dict(row)
        if result["payload_fingerprint"] != fingerprint:
            raise ValueError("idempotency key was already used with a different payload")
        result["created"] = False
        return result

    def add_forward_eps(
        self,
        observation: ForwardEPSObservation,
        idempotency_key: str,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = observation.canonical_payload()
        fingerprint = _fingerprint(payload)
        record_id = f"feps_{fingerprint[:24]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        with self._connect() as conn:
            existing = self._idempotent_existing(
                conn, "forward_eps_observations", idempotency_key, fingerprint
            )
            if existing:
                return existing
            self._validate_revision(
                conn, "forward_eps_observations", payload["logical_series_id"],
                payload["revision_number"], payload["revision_of"]
            )
            conn.execute(
                """
                INSERT INTO forward_eps_observations (
                    id,idempotency_key,payload_fingerprint,logical_series_id,
                    revision_number,revision_of,symbol,fiscal_year,eps_low,eps_base,
                    eps_high,source_name,source_type,published_at,available_at,
                    ingested_at,analyst_count,quality_note,status,unit
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id, idempotency_key, fingerprint,
                    payload["logical_series_id"], payload["revision_number"],
                    payload["revision_of"], payload["symbol"], payload["fiscal_year"],
                    payload["eps_low"], payload["eps_base"], payload["eps_high"],
                    payload["source_name"], payload["source_type"], payload["published_at"],
                    payload["available_at"], ingested, payload["analyst_count"],
                    payload["quality_note"], payload["status"], payload["unit"],
                ),
            )
            result = _row_dict(conn.execute(
                "SELECT * FROM forward_eps_observations WHERE id = ?", (record_id,)
            ).fetchone())
            result["created"] = True
            return result

    def add_pe_scenario(
        self,
        scenario: PEScenario,
        idempotency_key: str,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = scenario.canonical_payload()
        fingerprint = _fingerprint(payload)
        record_id = f"pe_{fingerprint[:24]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        with self._connect() as conn:
            existing = self._idempotent_existing(
                conn, "pe_scenarios", idempotency_key, fingerprint
            )
            if existing:
                return existing
            self._validate_revision(
                conn, "pe_scenarios", payload["logical_series_id"],
                payload["revision_number"], payload["revision_of"]
            )
            conn.execute(
                """
                INSERT INTO pe_scenarios (
                    id,idempotency_key,payload_fingerprint,logical_series_id,
                    revision_number,revision_of,label,pe_value,rationale,evidence_level,
                    scope,symbol,industry,market,available_at,ingested_at,
                    approval_status,approved_by,approved_at,effective_from,effective_to,version
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id, idempotency_key, fingerprint,
                    payload["logical_series_id"], payload["revision_number"],
                    payload["revision_of"], payload["label"], payload["pe_value"],
                    payload["rationale"], payload["evidence_level"], payload["scope"],
                    payload["symbol"], payload["industry"], payload["market"],
                    payload["available_at"], ingested, payload["approval_status"],
                    payload["approved_by"], payload["approved_at"],
                    payload["effective_from"], payload["effective_to"], payload["version"],
                ),
            )
            result = _row_dict(conn.execute(
                "SELECT * FROM pe_scenarios WHERE id = ?", (record_id,)
            ).fetchone())
            result["created"] = True
            return result

    @staticmethod
    def _validate_revision(
        conn: sqlite3.Connection,
        table: str,
        logical_series_id: str,
        revision_number: int,
        revision_of: str | None,
    ) -> None:
        if revision_number == 1:
            return
        previous = conn.execute(
            f"SELECT id, revision_number, logical_series_id FROM {table} WHERE id = ?",
            (revision_of,),
        ).fetchone()
        if previous is None:
            raise ValueError("revision_of does not exist")
        if previous["logical_series_id"] != logical_series_id:
            raise ValueError("revision_of belongs to another logical series")
        if int(previous["revision_number"]) != revision_number - 1:
            raise ValueError("revision_number must immediately follow revision_of")

    def forward_eps_as_of(self, symbol: str, knowledge_cutoff_at: str) -> list[dict[str, Any]]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY logical_series_id
                        ORDER BY revision_number DESC, available_at DESC, ingested_at DESC, id DESC
                    ) AS revision_rank
                    FROM forward_eps_observations
                    WHERE symbol = ? AND available_at <= ? AND ingested_at <= ?
                )
                SELECT * FROM ranked
                WHERE revision_rank = 1 AND status = 'active'
                ORDER BY fiscal_year, source_name, logical_series_id
                """,
                (symbol.strip().upper(), cutoff, cutoff),
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def pe_scenarios_as_of(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        industry: str | None = None,
        market: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY logical_series_id
                        ORDER BY revision_number DESC, available_at DESC, ingested_at DESC, id DESC
                    ) AS revision_rank
                    FROM pe_scenarios
                    WHERE available_at <= ? AND ingested_at <= ?
                )
                SELECT * FROM ranked
                WHERE revision_rank = 1 AND approval_status = 'approved'
                  AND approved_at <= ?
                  AND (effective_from IS NULL OR effective_from <= ?)
                  AND (effective_to IS NULL OR effective_to > ?)
                  AND ((scope = 'symbol' AND symbol = ?)
                    OR (scope = 'industry' AND industry = ?)
                    OR (scope = 'market' AND market = ?))
                ORDER BY CASE scope WHEN 'symbol' THEN 1 WHEN 'industry' THEN 2 ELSE 3 END,
                         label, logical_series_id
                """,
                (cutoff, cutoff, cutoff, cutoff, cutoff, symbol.strip().upper(), industry, market),
            ).fetchall()
        grouped = {scope.value: [] for scope in PEScope}
        for row in rows:
            grouped[row["scope"]].append(_row_dict(row))
        return grouped
