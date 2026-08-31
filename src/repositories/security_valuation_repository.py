"""Immutable, as-of-safe Phase 6 valuation-observation persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from src.domain.screening import SecurityValuationObservation
from src.domain.valuation import normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SecurityValuationRepository:
    def __init__(self, db_path: str = "data/cache.db", *, auto_migrate: bool = True):
        self.db_path = db_path
        if auto_migrate:
            apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _bind(
        conn: sqlite3.Connection,
        key: str,
        fingerprint: str,
        resource_id: str,
    ) -> None:
        conn.execute(
            "INSERT INTO screening_idempotency_keys VALUES (?,?,?,?,?)",
            (key, fingerprint, "valuation_observation", resource_id, utc_now_timestamp()),
        )

    def _existing(
        self, conn: sqlite3.Connection, key: str, fingerprint: str
    ) -> dict[str, Any] | None:
        binding = conn.execute(
            "SELECT * FROM screening_idempotency_keys WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if binding:
            if (
                binding["payload_fingerprint"] != fingerprint
                or binding["resource_type"] != "valuation_observation"
            ):
                raise ValueError("idempotency key was already used with a different payload")
            row = conn.execute(
                "SELECT * FROM security_valuation_observations WHERE id = ?",
                (binding["resource_id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("idempotency ledger references a missing resource")
            return {**dict(row), "created": False}
        duplicate = conn.execute(
            "SELECT * FROM security_valuation_observations WHERE payload_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if duplicate:
            self._bind(conn, key, fingerprint, duplicate["id"])
            return {**dict(duplicate), "created": False}
        return None

    def add_observation(
        self,
        observation: SecurityValuationObservation,
        idempotency_key: str,
        *,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = observation.canonical_payload()
        fingerprint = _fingerprint(payload)
        record_id = f"security_valuation_{fingerprint[:24]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        if ingested < payload["available_at"]:
            raise ValueError("ingested_at cannot precede available_at")
        with self._connect() as conn:
            existing = self._existing(conn, idempotency_key, fingerprint)
            if existing:
                return existing
            identity_owner = conn.execute(
                """
                SELECT logical_observation_id
                FROM security_valuation_observations
                WHERE symbol = ? AND metric_date = ?
                  AND source_name = ? AND source_dataset = ?
                LIMIT 1
                """,
                (
                    payload["symbol"],
                    payload["metric_date"],
                    payload["source_name"],
                    payload["source_dataset"],
                ),
            ).fetchone()
            if (
                identity_owner is not None
                and identity_owner["logical_observation_id"]
                != payload["logical_observation_id"]
            ):
                raise ValueError(
                    "valuation observation identity already belongs to another "
                    "logical observation"
                )
            previous = conn.execute(
                """
                SELECT * FROM security_valuation_observations
                WHERE logical_observation_id = ?
                ORDER BY revision_number DESC LIMIT 1
                """,
                (payload["logical_observation_id"],),
            ).fetchone()
            if payload["revision_number"] == 1:
                if previous is not None:
                    raise ValueError("logical observation already has a first revision")
            else:
                if previous is None or previous["id"] != payload["revision_of"]:
                    raise ValueError(
                        "revision_of must reference the latest valuation observation revision"
                    )
                if payload["revision_number"] != previous["revision_number"] + 1:
                    raise ValueError("revision_number must increment by one")
                for field in (
                    "symbol",
                    "metric_date",
                    "source_name",
                    "source_dataset",
                    "dividend_yield_unit",
                ):
                    if payload[field] != previous[field]:
                        raise ValueError(
                            f"valuation revision cannot change identity field: {field}"
                        )
            conn.execute(
                """
                INSERT INTO security_valuation_observations (
                    id,idempotency_key,payload_fingerprint,logical_observation_id,
                    revision_number,revision_of,symbol,metric_date,pe,pb,
                    dividend_yield_ratio,source_name,source_dataset,available_at,
                    ingested_at,status,dividend_yield_unit
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id,
                    idempotency_key,
                    fingerprint,
                    payload["logical_observation_id"],
                    payload["revision_number"],
                    payload["revision_of"],
                    payload["symbol"],
                    payload["metric_date"],
                    payload["pe"],
                    payload["pb"],
                    payload["dividend_yield_ratio"],
                    payload["source_name"],
                    payload["source_dataset"],
                    payload["available_at"],
                    ingested,
                    payload["status"],
                    payload["dividend_yield_unit"],
                ),
            )
            self._bind(conn, idempotency_key, fingerprint, record_id)
            result = dict(
                conn.execute(
                    "SELECT * FROM security_valuation_observations WHERE id = ?",
                    (record_id,),
                ).fetchone()
            )
            result["created"] = True
            return result

    def observation_states_as_of(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        *,
        source_name: str,
        source_dataset: str,
        window_start: str,
        window_end: str,
    ) -> list[dict[str, Any]]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            return self.observation_states_as_of_with_connection(
                conn,
                symbol,
                cutoff,
                source_name=source_name,
                source_dataset=source_dataset,
                window_start=window_start,
                window_end=window_end,
            )

    def observation_states_as_of_with_connection(
        self,
        conn: sqlite3.Connection,
        symbol: str,
        knowledge_cutoff_at: str,
        *,
        source_name: str,
        source_dataset: str,
        window_start: str,
        window_end: str,
    ) -> list[dict[str, Any]]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY logical_observation_id
                    ORDER BY revision_number DESC, available_at DESC,
                             ingested_at DESC, id DESC
                ) AS revision_rank
                FROM security_valuation_observations
                WHERE symbol = ?
                  AND source_name = ? AND source_dataset = ?
                  AND metric_date >= ? AND metric_date <= ?
                  AND available_at <= ? AND ingested_at <= ?
            )
            SELECT * FROM ranked
            WHERE revision_rank = 1
            ORDER BY metric_date, logical_observation_id
            """,
            (
                symbol.strip().upper(), source_name, source_dataset,
                window_start, window_end, cutoff, cutoff,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    def observations_as_of(self, *args, **kwargs) -> list[dict[str, Any]]:
        return [
            row
            for row in self.observation_states_as_of(*args, **kwargs)
            if row["status"] == "available"
        ]

    def observations_as_of_with_connection(
        self, conn: sqlite3.Connection, *args, **kwargs
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in self.observation_states_as_of_with_connection(
                conn, *args, **kwargs
            )
            if row["status"] == "available"
        ]
