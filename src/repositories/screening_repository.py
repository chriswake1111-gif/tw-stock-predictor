"""Immutable screening-profile revisions and approval events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from src.domain.screening import ScreeningProfileApproval, ScreeningProfileRevision
from src.domain.valuation import ApprovalStatus, normalize_utc_timestamp, utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ScreeningRepository:
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
        resource_type: str,
        resource_id: str,
    ) -> None:
        conn.execute(
            "INSERT INTO screening_idempotency_keys VALUES (?,?,?,?,?)",
            (key, fingerprint, resource_type, resource_id, utc_now_timestamp()),
        )

    def _existing(
        self,
        conn: sqlite3.Connection,
        *,
        key: str,
        fingerprint: str,
        resource_type: str,
        table: str,
        id_column: str,
    ) -> dict[str, Any] | None:
        binding = conn.execute(
            "SELECT * FROM screening_idempotency_keys WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if binding:
            if (
                binding["payload_fingerprint"] != fingerprint
                or binding["resource_type"] != resource_type
            ):
                raise ValueError("idempotency key was already used with a different payload")
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {id_column} = ?",
                (binding["resource_id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("idempotency ledger references a missing resource")
            return {**dict(row), "created": False}
        duplicate = conn.execute(
            f"SELECT * FROM {table} WHERE payload_fingerprint = ?", (fingerprint,)
        ).fetchone()
        if duplicate:
            self._bind(conn, key, fingerprint, resource_type, duplicate[id_column])
            return {**dict(duplicate), "created": False}
        return None

    def add_revision(
        self,
        revision: ScreeningProfileRevision,
        idempotency_key: str,
        *,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = revision.canonical_payload()
        fingerprint = _fingerprint(payload)
        record_id = f"screening_profile_{fingerprint[:24]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        if ingested < payload["available_at"]:
            raise ValueError("ingested_at cannot precede profile available_at")
        with self._connect() as conn:
            existing = self._existing(
                conn,
                key=idempotency_key,
                fingerprint=fingerprint,
                resource_type="profile_revision",
                table="screening_profile_revisions",
                id_column="id",
            )
            if existing:
                return existing
            previous = conn.execute(
                """
                SELECT * FROM screening_profile_revisions
                WHERE logical_profile_id = ?
                ORDER BY revision_number DESC LIMIT 1
                """,
                (payload["logical_profile_id"],),
            ).fetchone()
            if payload["revision_number"] == 1:
                if previous is not None:
                    raise ValueError("logical profile already has a first revision")
            else:
                if previous is None or previous["id"] != payload["revision_of"]:
                    raise ValueError(
                        "revision_of must reference the latest screening profile revision"
                    )
                if payload["revision_number"] != previous["revision_number"] + 1:
                    raise ValueError("profile revision_number must increment by one")
                for field in ("scope", "scope_value"):
                    if payload[field] != previous[field]:
                        raise ValueError(f"profile revision cannot change {field}")
            conn.execute(
                """
                INSERT INTO screening_profile_revisions (
                    id,idempotency_key,payload_fingerprint,logical_profile_id,
                    revision_number,revision_of,scope,scope_value,valuation_basis,
                    valuation_source_name,valuation_source_dataset,pe_percentile_max,
                    pb_percentile_max,dividend_yield_percentile_min,history_years,
                    minimum_observations,forward_eps_growth_required,
                    forward_eps_source_name,forward_eps_source_type,
                    forward_eps_growth_convention,technical_component,available_at,
                    ingested_at,created_by,rationale,status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id,
                    idempotency_key,
                    fingerprint,
                    payload["logical_profile_id"],
                    payload["revision_number"],
                    payload["revision_of"],
                    payload["scope"],
                    payload["scope_value"],
                    payload["valuation_basis"],
                    payload["valuation_source_name"],
                    payload["valuation_source_dataset"],
                    payload["pe_percentile_max"],
                    payload["pb_percentile_max"],
                    payload["dividend_yield_percentile_min"],
                    payload["history_years"],
                    payload["minimum_observations"],
                    int(payload["forward_eps_growth_required"]),
                    payload["forward_eps_source_name"],
                    payload["forward_eps_source_type"],
                    payload["forward_eps_growth_convention"],
                    payload["technical_component"],
                    payload["available_at"],
                    ingested,
                    payload["created_by"],
                    payload["rationale"],
                    payload["status"],
                ),
            )
            self._bind(
                conn, idempotency_key, fingerprint, "profile_revision", record_id
            )
            result = dict(
                conn.execute(
                    "SELECT * FROM screening_profile_revisions WHERE id = ?",
                    (record_id,),
                ).fetchone()
            )
            result["created"] = True
            return result

    def add_approval(
        self,
        approval: ScreeningProfileApproval,
        idempotency_key: str,
        *,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = approval.canonical_payload()
        fingerprint = _fingerprint(payload)
        event_id = f"screening_approval_event_{fingerprint[:20]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        if ingested < payload["approved_at"]:
            raise ValueError("ingested_at cannot precede approval approved_at")
        with self._connect() as conn:
            existing = self._existing(
                conn,
                key=idempotency_key,
                fingerprint=fingerprint,
                resource_type="profile_approval",
                table="screening_profile_approvals",
                id_column="approval_event_id",
            )
            if existing:
                return existing
            profile = conn.execute(
                "SELECT * FROM screening_profile_revisions WHERE id = ?",
                (payload["profile_revision_id"],),
            ).fetchone()
            if profile is None:
                raise ValueError("screening profile revision does not exist")
            if payload["approved_at"] < profile["available_at"]:
                raise ValueError("approval approved_at cannot precede profile available_at")
            if (
                payload["decision"] == ApprovalStatus.APPROVED.value
                and profile["status"] != "available"
            ):
                raise ValueError("a revoked profile revision cannot be approved")
            previous = conn.execute(
                """
                SELECT approved_at FROM screening_profile_approvals
                WHERE profile_revision_id = ? AND rule_id = 'SEL-01'
                ORDER BY approved_at DESC, ingested_at DESC, approval_event_id DESC
                LIMIT 1
                """,
                (payload["profile_revision_id"],),
            ).fetchone()
            if previous and payload["approved_at"] < previous["approved_at"]:
                raise ValueError("approval events cannot be backdated")
            conn.execute(
                """
                INSERT INTO screening_profile_approvals (
                    approval_event_id,approval_id,payload_fingerprint,
                    profile_revision_id,decision,rule_id,rule_version,evidence_level,
                    implementation_mode,project_operationalization,approved_by,
                    rationale,approved_at,ingested_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    payload["approval_id"],
                    fingerprint,
                    payload["profile_revision_id"],
                    payload["decision"],
                    payload["rule_id"],
                    payload["rule_version"],
                    payload["evidence_level"],
                    payload["implementation_mode"],
                    int(payload["project_operationalization"]),
                    payload["approved_by"],
                    payload["rationale"],
                    payload["approved_at"],
                    ingested,
                ),
            )
            self._bind(
                conn, idempotency_key, fingerprint, "profile_approval", event_id
            )
            result = dict(
                conn.execute(
                    "SELECT * FROM screening_profile_approvals WHERE approval_event_id = ?",
                    (event_id,),
                ).fetchone()
            )
            result["created"] = True
            return result

    def effective_profile_states_as_of(
        self, knowledge_cutoff_at: str
    ) -> list[dict[str, Any]]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        with self._connect() as conn:
            return self.effective_profile_states_as_of_with_connection(conn, cutoff)

    def effective_profile_states_as_of_with_connection(
        self, conn: sqlite3.Connection, knowledge_cutoff_at: str
    ) -> list[dict[str, Any]]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY logical_profile_id
                    ORDER BY revision_number DESC, available_at DESC,
                             ingested_at DESC, id DESC
                ) AS revision_rank
                FROM screening_profile_revisions
                WHERE available_at <= ? AND ingested_at <= ?
            ), approval_ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY profile_revision_id
                    ORDER BY approved_at DESC, ingested_at DESC,
                             approval_event_id DESC
                ) AS approval_rank
                FROM screening_profile_approvals
                WHERE approved_at <= ? AND ingested_at <= ?
            )
            SELECT ranked.*,
                   approval_ranked.approval_id AS verified_approval_id,
                   approval_ranked.decision AS effective_approval_status,
                   approval_ranked.rule_id AS approval_rule_id,
                   approval_ranked.rule_version AS approval_rule_version,
                   approval_ranked.evidence_level AS approved_evidence_level,
                   approval_ranked.implementation_mode AS approved_implementation_mode,
                   approval_ranked.project_operationalization,
                   approval_ranked.approved_by AS verified_approved_by,
                   approval_ranked.rationale AS approval_rationale
            FROM ranked LEFT JOIN approval_ranked
              ON approval_ranked.profile_revision_id = ranked.id
             AND approval_ranked.approval_rank = 1
            WHERE ranked.revision_rank = 1
            ORDER BY ranked.logical_profile_id
            """,
            (cutoff, cutoff, cutoff, cutoff),
        ).fetchall()
        return [dict(row) for row in rows]
