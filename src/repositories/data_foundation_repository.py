"""Transactional persistence for Phase 10 operational data evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from src.domain.data_foundation import (
    DataProvider,
    DataResource,
    IngestionRun,
    IngestionRunItem,
    RawResourceRevision,
    ResourcePublicationEvidence,
    canonical_json,
    sha256_text,
)
from src.domain.valuation import normalize_utc_timestamp, parse_aware_timestamp
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
        self,
        resource_id: str,
        owner_run_id: str,
        acquired_at: str,
        *,
        lease_seconds: int = 900,
    ) -> dict[str, Any]:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        acquired = normalize_utc_timestamp(acquired_at, "acquired_at")
        acquired_time = parse_aware_timestamp(acquired, "acquired_at")
        lease_expires = normalize_utc_timestamp(
            (acquired_time + timedelta(seconds=lease_seconds)).isoformat(),
            "lease_expires_at",
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM ingestion_resource_locks WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO ingestion_resource_locks VALUES (?,?,?,?)",
                    (resource_id, owner_run_id, acquired, lease_expires),
                )
                return dict(conn.execute(
                    "SELECT * FROM ingestion_resource_locks WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone())
            if parse_aware_timestamp(
                existing["lease_expires_at"], "lease_expires_at"
            ) > acquired_time:
                raise RuntimeError("resource ingestion is already locked")
            owner = conn.execute(
                "SELECT * FROM ingestion_runs WHERE ingestion_run_id = ?",
                (existing["owner_run_id"],),
            ).fetchone()
            if owner is None:
                raise RuntimeError("resource lock owner run is missing")
            if parse_aware_timestamp(owner["started_at"], "started_at") > acquired_time:
                raise RuntimeError("resource lock recovery precedes owner run")
            previous_status = owner["status"]
            action = "terminal_owner_lock_reclaimed"
            if previous_status == "running":
                conn.execute(
                    """
                    UPDATE ingestion_runs
                    SET completed_at = ?, status = 'failed'
                    WHERE ingestion_run_id = ? AND status = 'running'
                    """,
                    (acquired, existing["owner_run_id"]),
                )
                action = "run_marked_failed_and_lock_reclaimed"
            event_identity = canonical_json({
                "resource_id": resource_id,
                "previous_owner_run_id": existing["owner_run_id"],
                "recovering_run_id": owner_run_id,
                "previous_lease_expires_at": existing["lease_expires_at"],
                "recovered_at": acquired,
            })
            recovery_event_id = f"lock_recovery_{sha256_text(event_identity)[:24]}"
            conn.execute(
                """
                INSERT INTO ingestion_lock_recovery_events VALUES (
                    ?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    recovery_event_id, resource_id, existing["owner_run_id"],
                    owner_run_id, existing["acquired_at"],
                    existing["lease_expires_at"], acquired, previous_status,
                    action, "orphaned_resource_lock_recovered",
                ),
            )
            conn.execute(
                """
                UPDATE ingestion_resource_locks
                SET owner_run_id = ?, acquired_at = ?, lease_expires_at = ?
                WHERE resource_id = ? AND owner_run_id = ?
                """,
                (
                    owner_run_id, acquired, lease_expires, resource_id,
                    existing["owner_run_id"],
                ),
            )
            return {
                **dict(conn.execute(
                    "SELECT * FROM ingestion_resource_locks WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()),
                "recovery_event_id": recovery_event_id,
            }

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

    def add_publication_evidence(
        self,
        evidence: ResourcePublicationEvidence,
        *,
        ingested_at: str,
    ) -> dict[str, Any]:
        payload = evidence.canonical_payload()
        ingested = normalize_utc_timestamp(ingested_at, "ingested_at")
        if parse_aware_timestamp(ingested, "ingested_at") < parse_aware_timestamp(
            payload["captured_at"], "captured_at"
        ):
            raise ValueError("publication evidence ingested_at cannot precede captured_at")
        fingerprint = sha256_text(canonical_json(payload))
        evidence_id = evidence.deterministic_identity()
        with self._connect() as conn:
            authority = conn.execute(
                """
                SELECT p.authority_tier, r.provider_id AS resource_provider_id
                FROM data_providers p
                JOIN data_resources r ON r.resource_id = ?
                WHERE p.provider_id = ?
                """,
                (payload["resource_id"], payload["provider_id"]),
            ).fetchone()
            if (
                authority is None
                or authority["authority_tier"] != "authoritative"
                or authority["resource_provider_id"] != payload["provider_id"]
            ):
                raise ValueError("publication evidence requires its authoritative provider")
            existing = conn.execute(
                "SELECT * FROM resource_publication_evidence WHERE identity_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing:
                return {**dict(existing), "created": False}
            previous = conn.execute(
                """
                SELECT * FROM resource_publication_evidence
                WHERE provider_id = ? AND resource_id = ?
                  AND logical_revision_key = ?
                ORDER BY revision_number DESC, ingested_at DESC,
                         publication_evidence_id DESC
                LIMIT 1
                """,
                (
                    payload["provider_id"], payload["resource_id"],
                    payload["logical_revision_key"],
                ),
            ).fetchone()
            revision_number = int(previous["revision_number"]) + 1 if previous else 1
            conn.execute(
                """
                INSERT INTO resource_publication_evidence VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    evidence_id, fingerprint, payload["provider_id"],
                    payload["resource_id"], payload["logical_revision_key"],
                    payload["official_release_at"], payload["source_reference"],
                    payload["source_identity"], payload["evidence_file_sha256"],
                    payload["captured_at"], payload["verification_mode"],
                    payload["verified_by"], payload["status"], revision_number,
                    previous["publication_evidence_id"] if previous else None,
                    ingested, ingested,
                ),
            )
            return {
                **dict(conn.execute(
                    "SELECT * FROM resource_publication_evidence WHERE publication_evidence_id = ?",
                    (evidence_id,),
                ).fetchone()),
                "created": True,
            }

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
                and latest["session_status"] == session_status
                and latest["status"] == status
                and latest["note"] == note
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

    def provider_health_as_of(
        self,
        knowledge_cutoff_at: str,
        *,
        provider_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = normalize_utc_timestamp(
            knowledge_cutoff_at, "knowledge_cutoff_at"
        )
        clauses = []
        parameters: list[Any] = [cutoff, cutoff, cutoff, cutoff, cutoff, cutoff]
        if provider_id:
            clauses.append("r.provider_id = ?")
            parameters.append(provider_id.strip().lower())
        if resource_id:
            clauses.append("r.resource_id = ?")
            parameters.append(resource_id.strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                WITH latest_item AS (
                    SELECT i.*, ROW_NUMBER() OVER (
                        PARTITION BY i.resource_id
                        ORDER BY i.started_at DESC, i.ingestion_run_item_id DESC
                    ) AS rank_no
                    FROM ingestion_run_items i
                    WHERE i.started_at <= ?
                ), successes AS (
                    SELECT resource_id, MAX(completed_at) AS last_success_at
                    FROM ingestion_run_items
                    WHERE completed_at <= ? AND status IN (
                        'accepted','partial','awaiting_review','quality_warning'
                    )
                    GROUP BY resource_id
                ), visible_raw AS (
                    SELECT raw.*, ROW_NUMBER() OVER (
                        PARTITION BY raw.resource_id, raw.logical_revision_key
                        ORDER BY raw.ingested_at DESC, raw.available_at DESC,
                                 raw.raw_resource_revision_id DESC
                    ) AS rank_no
                    FROM raw_resource_revisions raw
                    WHERE raw.available_at <= ? AND raw.ingested_at <= ?
                ), effective_eligible AS (
                    SELECT * FROM visible_raw
                    WHERE rank_no = 1 AND eligibility_status = 'eligible'
                ), latest_business_key AS (
                    SELECT resource_id, MAX(logical_revision_key) AS logical_revision_key
                    FROM effective_eligible GROUP BY resource_id
                ), latest_eligible AS (
                    SELECT eligible.*
                    FROM effective_eligible eligible
                    JOIN latest_business_key business
                      ON business.resource_id = eligible.resource_id
                     AND business.logical_revision_key = eligible.logical_revision_key
                ), visible_calendar AS (
                    SELECT calendar.*, ROW_NUMBER() OVER (
                        PARTITION BY market, trade_date
                        ORDER BY revision_number DESC, available_at DESC,
                                 ingested_at DESC, calendar_revision_id DESC
                    ) AS rank_no
                    FROM trading_calendar_revisions calendar
                    WHERE market = 'TW' AND available_at <= ? AND ingested_at <= ?
                ), expected_session AS (
                    SELECT MAX(trade_date) AS trade_date
                    FROM visible_calendar
                    WHERE rank_no = 1 AND status = 'available'
                      AND session_status IN ('trading', 'special')
                )
                SELECT r.*, p.display_name, p.authority_tier, p.provider_type,
                       li.started_at AS last_attempt_at,
                       s.last_success_at,
                       le.available_at AS last_eligible_revision_at,
                       le.logical_revision_key AS last_eligible_logical_key,
                       li.status AS latest_item_status,
                       li.quality_status AS operational_status,
                       li.reason AS latest_error,
                       (SELECT trade_date FROM expected_session)
                           AS latest_expected_trade_date
                FROM data_resources r
                JOIN data_providers p ON p.provider_id = r.provider_id
                LEFT JOIN latest_item li
                  ON li.resource_id = r.resource_id AND li.rank_no = 1
                LEFT JOIN successes s ON s.resource_id = r.resource_id
                LEFT JOIN latest_eligible le
                  ON le.resource_id = r.resource_id
                {where}
                ORDER BY r.provider_id, r.resource_id
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]
