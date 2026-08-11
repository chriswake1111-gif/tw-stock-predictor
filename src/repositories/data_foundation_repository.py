"""Transactional persistence for Phase 10 operational data evidence."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.domain.data_foundation import (
    DataProvider,
    DataResource,
    IngestionRun,
    IngestionRunItem,
    RawResourceRevision,
    canonical_json,
    sha256_text,
)
from src.domain.valuation import normalize_utc_timestamp
from src.repositories.migration_runner import apply_valuation_migration


class DataFoundationRepository:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def register_provider(self, provider: DataProvider) -> dict[str, Any]:
        payload = provider.canonical_payload()
        fingerprint = sha256_text(canonical_json(payload))
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM data_providers WHERE provider_id = ?",
                (payload["provider_id"],),
            ).fetchone()
            if existing:
                if existing["payload_fingerprint"] != fingerprint:
                    raise ValueError("provider identity already exists with different metadata")
                return {**dict(existing), "created": False}
            conn.execute(
                """
                INSERT INTO data_providers VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    payload["provider_id"], payload["display_name"],
                    payload["authority_tier"], payload["provider_type"],
                    payload["base_identity"], int(payload["enabled"]),
                    payload["created_at"], fingerprint,
                ),
            )
            row = conn.execute(
                "SELECT * FROM data_providers WHERE provider_id = ?",
                (payload["provider_id"],),
            ).fetchone()
            return {**dict(row), "created": True}

    def register_resource(self, resource: DataResource) -> dict[str, Any]:
        payload = resource.canonical_payload()
        fingerprint = sha256_text(canonical_json(payload))
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM data_resources WHERE resource_id = ?",
                (payload["resource_id"],),
            ).fetchone()
            if existing:
                if existing["payload_fingerprint"] != fingerprint:
                    raise ValueError("resource identity already exists with different metadata")
                return {**dict(existing), "created": False}
            conn.execute(
                """
                INSERT INTO data_resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payload["resource_id"], payload["provider_id"],
                    payload["logical_resource_key"], payload["resource_type"],
                    payload["market"], payload["expected_frequency"],
                    payload["freshness_policy"], payload["parser_id"],
                    payload["parser_version"], payload["schema_version"],
                    payload["storage_policy"], int(payload["enabled"]),
                    payload["created_at"], fingerprint,
                ),
            )
            row = conn.execute(
                "SELECT * FROM data_resources WHERE resource_id = ?",
                (payload["resource_id"],),
            ).fetchone()
            return {**dict(row), "created": True}

    def add_run(self, run: IngestionRun) -> dict[str, Any]:
        payload = run.canonical_payload()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM ingestion_runs WHERE ingestion_run_id = ?",
                (payload["ingestion_run_id"],),
            ).fetchone()
            if existing:
                raise ValueError("ingestion_run_id already exists")
            conn.execute(
                """
                INSERT INTO ingestion_runs VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    payload["ingestion_run_id"], payload["started_at"],
                    payload["completed_at"], payload["trigger_type"],
                    payload["runner_version"],
                    canonical_json(payload["requested_resources"]),
                    payload["actor_id"], payload["status"],
                    payload["retry_of_run_id"],
                ),
            )
            row = conn.execute(
                "SELECT * FROM ingestion_runs WHERE ingestion_run_id = ?",
                (payload["ingestion_run_id"],),
            ).fetchone()
            return dict(row)

    def complete_run(self, run: IngestionRun) -> dict[str, Any]:
        payload = run.canonical_payload()
        if payload["status"] == "running":
            raise ValueError("complete_run requires a terminal status")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM ingestion_runs WHERE ingestion_run_id = ?",
                (payload["ingestion_run_id"],),
            ).fetchone()
            if current is None:
                raise ValueError("ingestion run does not exist")
            if current["status"] != "running":
                raise ValueError("ingestion run is already complete")
            immutable = {
                "started_at": payload["started_at"],
                "trigger_type": payload["trigger_type"],
                "runner_version": payload["runner_version"],
                "requested_resources_json": canonical_json(
                    payload["requested_resources"]
                ),
                "actor_id": payload["actor_id"],
                "retry_of_run_id": payload["retry_of_run_id"],
            }
            for field, expected in immutable.items():
                if current[field] != expected:
                    raise ValueError(f"completed run changed immutable field {field}")
            conn.execute(
                """
                UPDATE ingestion_runs
                SET completed_at = ?, status = ?
                WHERE ingestion_run_id = ? AND status = 'running'
                """,
                (
                    payload["completed_at"], payload["status"],
                    payload["ingestion_run_id"],
                ),
            )
            return dict(conn.execute(
                "SELECT * FROM ingestion_runs WHERE ingestion_run_id = ?",
                (payload["ingestion_run_id"],),
            ).fetchone())

    def add_run_item(self, item: IngestionRunItem) -> dict[str, Any]:
        payload = item.canonical_payload()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_run_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payload["ingestion_run_item_id"], payload["ingestion_run_id"],
                    payload["provider_id"], payload["resource_id"],
                    payload["started_at"], payload["completed_at"],
                    payload["status"], payload["http_status"],
                    payload["raw_payload_sha256"], payload["parser_version"],
                    payload["schema_fingerprint"], payload["record_count"],
                    payload["accepted_count"], payload["rejected_count"],
                    payload["quality_status"], payload["reason"],
                ),
            )
            return dict(conn.execute(
                "SELECT * FROM ingestion_run_items WHERE ingestion_run_item_id = ?",
                (payload["ingestion_run_item_id"],),
            ).fetchone())

    def add_raw_revision(self, revision: RawResourceRevision) -> dict[str, Any]:
        payload = revision.canonical_payload()
        fingerprint = revision.deterministic_identity()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM raw_resource_revisions WHERE identity_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing:
                return {**dict(existing), "created": False}
            previous = conn.execute(
                """
                SELECT * FROM raw_resource_revisions
                WHERE provider_id = ? AND resource_id = ? AND logical_revision_key = ?
                ORDER BY ingested_at DESC, raw_resource_revision_id DESC LIMIT 1
                """,
                (
                    payload["provider_id"], payload["resource_id"],
                    payload["logical_revision_key"],
                ),
            ).fetchone()
            if previous and payload["supersedes_revision_id"] != previous["raw_resource_revision_id"]:
                raise ValueError(
                    "corrected source payload must explicitly supersede the latest revision"
                )
            if not previous and payload["supersedes_revision_id"] is not None:
                raise ValueError("first source revision cannot supersede another revision")
            conn.execute(
                """
                INSERT INTO raw_resource_revisions VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    payload["raw_resource_revision_id"], fingerprint,
                    payload["provider_id"], payload["resource_id"],
                    payload["logical_revision_key"], payload["source_published_at"],
                    payload["available_at"], payload["received_at"],
                    payload["ingested_at"], payload["raw_payload_sha256"],
                    payload["parser_version"], payload["schema_fingerprint"],
                    payload["storage_policy"], payload["storage_location"],
                    payload["quality_status"], payload["eligibility_status"],
                    payload["supersedes_revision_id"], payload["reason"],
                ),
            )
            return {**dict(conn.execute(
                "SELECT * FROM raw_resource_revisions WHERE raw_resource_revision_id = ?",
                (payload["raw_resource_revision_id"],),
            ).fetchone()), "created": True}

    def acquire_resource_lock(
        self, resource_id: str, owner_run_id: str, acquired_at: str
    ) -> None:
        acquired = normalize_utc_timestamp(acquired_at, "acquired_at")
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO ingestion_resource_locks VALUES (?,?,?)",
                    (resource_id, owner_run_id, acquired),
                )
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("resource ingestion is already locked") from exc

    def release_resource_lock(self, resource_id: str, owner_run_id: str) -> None:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM ingestion_resource_locks WHERE resource_id = ? AND owner_run_id = ?",
                (resource_id, owner_run_id),
            )
            if result.rowcount != 1:
                raise RuntimeError("resource lock is not owned by this run")

    def raw_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM raw_resource_revisions WHERE raw_resource_revision_id = ?",
                (revision_id,),
            ).fetchone())

    def latest_raw_revision(
        self, provider_id: str, resource_id: str, logical_revision_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._row(conn.execute(
                """
                SELECT * FROM raw_resource_revisions
                WHERE provider_id = ? AND resource_id = ? AND logical_revision_key = ?
                ORDER BY ingested_at DESC, raw_resource_revision_id DESC
                LIMIT 1
                """,
                (provider_id, resource_id, logical_revision_key),
            ).fetchone())

    def add_calendar_revision(
        self,
        *,
        calendar_revision_id: str,
        raw_resource_revision_id: str,
        market: str,
        trade_date: str,
        session_status: str,
        available_at: str,
        ingested_at: str,
        note: str | None = None,
        status: str = "available",
    ) -> dict[str, Any]:
        available = normalize_utc_timestamp(available_at, "available_at")
        ingested = normalize_utc_timestamp(ingested_at, "ingested_at")
        with self._connect() as conn:
            latest = conn.execute(
                """
                SELECT * FROM trading_calendar_revisions
                WHERE market = ? AND trade_date = ?
                ORDER BY revision_number DESC, available_at DESC,
                         ingested_at DESC, calendar_revision_id DESC
                LIMIT 1
                """,
                (market.upper(), trade_date),
            ).fetchone()
            if (
                latest
                and latest["raw_resource_revision_id"] == raw_resource_revision_id
                and latest["session_status"] == session_status
                and latest["status"] == status
            ):
                return {**dict(latest), "created": False}
            revision_number = int(latest["revision_number"]) + 1 if latest else 1
            conn.execute(
                """
                INSERT INTO trading_calendar_revisions (
                    calendar_revision_id, raw_resource_revision_id, market,
                    trade_date, session_status, available_at, ingested_at,
                    revision_number, status, note
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    calendar_revision_id, raw_resource_revision_id, market.upper(),
                    trade_date, session_status, available, ingested,
                    revision_number, status, note,
                ),
            )
            row = conn.execute(
                "SELECT * FROM trading_calendar_revisions WHERE calendar_revision_id = ?",
                (calendar_revision_id,),
            ).fetchone()
            return {**dict(row), "created": True}

    def calendar_session_as_of(
        self, market: str, trade_date: str, knowledge_cutoff_at: str
    ) -> dict[str, Any] | None:
        cutoff = normalize_utc_timestamp(
            knowledge_cutoff_at, "knowledge_cutoff_at"
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM trading_calendar_revisions
                WHERE market = ? AND trade_date = ?
                  AND available_at <= ? AND ingested_at <= ?
                ORDER BY revision_number DESC, available_at DESC,
                         ingested_at DESC, calendar_revision_id DESC
                LIMIT 1
                """,
                (market.upper(), trade_date, cutoff, cutoff),
            ).fetchone()
        if row is None or row["status"] == "revoked":
            return None
        return dict(row)
