"""Immutable, as-of-safe persistence for Forward EPS and PE scenarios."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from src.domain.valuation import (
    ApprovalResourceType,
    ForwardEPSObservation,
    PEScenario,
    PEScope,
    ValuationApproval,
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
        self,
        conn: sqlite3.Connection,
        table: str,
        id_column: str,
        resource_type: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        binding = conn.execute(
            "SELECT * FROM valuation_idempotency_keys WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if binding:
            if (
                binding["payload_fingerprint"] != fingerprint
                or binding["resource_type"] != resource_type
            ):
                raise ValueError(
                    "idempotency key was already used with a different payload"
                )
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {id_column} = ?",
                (binding["resource_id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("idempotency ledger references a missing resource")
            result = _row_dict(row)
            result["created"] = False
            return result
        row = conn.execute(
            f"SELECT * FROM {table} WHERE payload_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        self._bind_idempotency(
            conn,
            idempotency_key,
            fingerprint,
            resource_type,
            row[id_column],
        )
        result = _row_dict(row)
        result["created"] = False
        return result

    @staticmethod
    def _bind_idempotency(
        conn: sqlite3.Connection,
        idempotency_key: str,
        fingerprint: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO valuation_idempotency_keys (
                idempotency_key,payload_fingerprint,resource_type,resource_id,bound_at
            ) VALUES (?,?,?,?,?)
            """,
            (idempotency_key, fingerprint, resource_type, resource_id, utc_now_timestamp()),
        )

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
                conn, "forward_eps_observations", "id", "forward_eps",
                idempotency_key, fingerprint
            )
            if existing:
                return existing
            self._validate_revision(
                conn, "forward_eps_observations", payload["logical_series_id"],
                payload["revision_number"], payload["revision_of"], payload,
                ("symbol", "fiscal_year", "source_name", "source_type", "unit"),
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
            self._bind_idempotency(
                conn, idempotency_key, fingerprint, "forward_eps", record_id
            )
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
                conn, "pe_scenarios", "id", "pe_scenario",
                idempotency_key, fingerprint
            )
            if existing:
                return existing
            self._validate_revision(
                conn, "pe_scenarios", payload["logical_series_id"],
                payload["revision_number"], payload["revision_of"], payload,
                ("scope", "symbol", "industry", "market"),
            )
            conn.execute(
                """
                INSERT INTO pe_scenarios (
                    id,idempotency_key,payload_fingerprint,logical_series_id,
                    revision_number,revision_of,label,pe_value,rationale,evidence_level,
                    scope,symbol,industry,market,available_at,ingested_at,
                    approval_status,approved_by,approved_at,effective_from,effective_to,
                    evidence_basis_rule_id,version
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id, idempotency_key, fingerprint,
                    payload["logical_series_id"], payload["revision_number"],
                    payload["revision_of"], payload["label"], payload["pe_value"],
                    payload["rationale"], payload["evidence_level"], payload["scope"],
                    payload["symbol"], payload["industry"], payload["market"],
                    payload["available_at"], ingested, payload["approval_status"],
                    payload["approved_by"], payload["approved_at"],
                    payload["effective_from"], payload["effective_to"],
                    payload["evidence_basis_rule_id"], payload["version"],
                ),
            )
            result = _row_dict(conn.execute(
                "SELECT * FROM pe_scenarios WHERE id = ?", (record_id,)
            ).fetchone())
            self._bind_idempotency(
                conn, idempotency_key, fingerprint, "pe_scenario", record_id
            )
            result["created"] = True
            return result

    def add_approval(
        self,
        approval: ValuationApproval,
        idempotency_key: str,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        payload = approval.canonical_payload()
        fingerprint = _fingerprint(payload)
        event_id = f"approval_{fingerprint[:24]}"
        ingested = normalize_utc_timestamp(
            ingested_at or utc_now_timestamp(), "ingested_at"
        )
        resource_table = (
            "forward_eps_observations"
            if approval.resource_type is ApprovalResourceType.FORWARD_EPS
            else "pe_scenarios"
        )
        with self._connect() as conn:
            existing = self._idempotent_existing(
                conn, "valuation_approvals", "approval_event_id", "approval",
                idempotency_key, fingerprint
            )
            if existing:
                return existing
            resource = conn.execute(
                f"SELECT * FROM {resource_table} WHERE id = ?",
                (approval.resource_id,),
            ).fetchone()
            if resource is None:
                raise ValueError("approval resource does not exist")
            previous_decision = conn.execute(
                """
                SELECT available_at FROM valuation_approvals
                WHERE resource_type = ? AND resource_id = ?
                ORDER BY available_at DESC, ingested_at DESC, approval_event_id DESC
                LIMIT 1
                """,
                (payload["resource_type"], payload["resource_id"]),
            ).fetchone()
            if (
                previous_decision is not None
                and payload["available_at"] < previous_decision["available_at"]
            ):
                raise ValueError(
                    "approval available_at cannot precede the previous decision"
                )
            if (
                approval.resource_type is ApprovalResourceType.PE_SCENARIO
                and approval.decision.value == "approved"
            ):
                if not str(resource["label"] or "").strip():
                    raise ValueError("approved PE scenarios require a non-blank label")
                if not str(resource["rationale"] or "").strip():
                    raise ValueError("approved PE scenarios require a non-blank rationale")
            conn.execute(
                """
                INSERT INTO valuation_approvals (
                    approval_event_id,approval_id,payload_fingerprint,resource_type,
                    resource_id,decision,rule_id,evidence_level,
                    project_operationalization,approved_by,rationale,available_at,ingested_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id, payload["approval_id"], fingerprint,
                    payload["resource_type"], payload["resource_id"],
                    payload["decision"], payload["rule_id"], payload["evidence_level"],
                    int(payload["project_operationalization"]), payload["approved_by"],
                    payload["rationale"], payload["available_at"], ingested,
                ),
            )
            self._bind_idempotency(
                conn, idempotency_key, fingerprint, "approval", event_id
            )
            result = _row_dict(conn.execute(
                "SELECT * FROM valuation_approvals WHERE approval_event_id = ?",
                (event_id,),
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
        payload: dict[str, Any],
        identity_fields: tuple[str, ...],
    ) -> None:
        if revision_number == 1:
            existing = conn.execute(
                f"SELECT 1 FROM {table} WHERE logical_series_id = ? LIMIT 1",
                (logical_series_id,),
            ).fetchone()
            if existing:
                raise ValueError("logical_series_id already has a first revision")
            return
        previous = conn.execute(
            f"SELECT * FROM {table} WHERE id = ?",
            (revision_of,),
        ).fetchone()
        if previous is None:
            raise ValueError("revision_of does not exist")
        if previous["logical_series_id"] != logical_series_id:
            raise ValueError("revision_of belongs to another logical series")
        if int(previous["revision_number"]) != revision_number - 1:
            raise ValueError("revision_number must immediately follow revision_of")
        for field in identity_fields:
            if previous[field] != payload[field]:
                raise ValueError(f"revision cannot change identity field: {field}")

    def forward_eps_state_as_of(
        self, symbol: str, knowledge_cutoff_at: str
    ) -> list[dict[str, Any]]:
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
                , approval_ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY resource_id
                        ORDER BY available_at DESC, ingested_at DESC, approval_event_id DESC
                    ) AS approval_rank
                    FROM valuation_approvals
                    WHERE resource_type = 'forward_eps'
                      AND available_at <= ? AND ingested_at <= ?
                )
                SELECT ranked.*,
                       approval_ranked.approval_id AS verified_approval_id,
                       approval_ranked.decision AS effective_approval_status,
                       approval_ranked.rule_id AS approval_rule_id,
                       approval_ranked.evidence_level AS approved_evidence_level,
                       approval_ranked.project_operationalization,
                       approval_ranked.approved_by AS verified_approved_by,
                       approval_ranked.rationale AS approval_rationale
                FROM ranked LEFT JOIN approval_ranked
                  ON approval_ranked.resource_id = ranked.id
                 AND approval_ranked.approval_rank = 1
                WHERE ranked.revision_rank = 1 AND ranked.status = 'active'
                ORDER BY fiscal_year, source_name, logical_series_id
                """,
                (symbol.strip().upper(), cutoff, cutoff, cutoff, cutoff),
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def forward_eps_as_of(
        self, symbol: str, knowledge_cutoff_at: str
    ) -> list[dict[str, Any]]:
        return [
            row for row in self.forward_eps_state_as_of(symbol, knowledge_cutoff_at)
            if row["effective_approval_status"] == "approved"
            and row["approval_rule_id"] == "VAL-02"
            and row["approved_evidence_level"] != "U"
            and (
                row["approved_evidence_level"] != "C"
                or row["project_operationalization"] == 1
            )
        ]

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
                , approval_ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY resource_id
                        ORDER BY available_at DESC, ingested_at DESC, approval_event_id DESC
                    ) AS approval_rank
                    FROM valuation_approvals
                    WHERE resource_type = 'pe_scenario'
                      AND available_at <= ? AND ingested_at <= ?
                )
                SELECT ranked.*,
                       approval_ranked.approval_id AS verified_approval_id,
                       approval_ranked.decision AS effective_approval_status,
                       approval_ranked.rule_id AS approval_rule_id,
                       approval_ranked.evidence_level AS approved_evidence_level,
                       approval_ranked.project_operationalization,
                       approval_ranked.approved_by AS verified_approved_by,
                       approval_ranked.rationale AS approval_rationale
                FROM ranked JOIN approval_ranked
                  ON approval_ranked.resource_id = ranked.id
                 AND approval_ranked.approval_rank = 1
                WHERE ranked.revision_rank = 1
                  AND approval_ranked.decision = 'approved'
                  AND approval_ranked.rule_id = 'VAL-04'
                  AND approval_ranked.evidence_level != 'U'
                  AND (approval_ranked.evidence_level != 'C'
                       OR approval_ranked.project_operationalization = 1)
                  AND (ranked.effective_from IS NULL OR ranked.effective_from <= ?)
                  AND (ranked.effective_to IS NULL OR ranked.effective_to > ?)
                  AND ((scope = 'symbol' AND symbol = ?)
                    OR (scope = 'industry' AND industry = ?)
                    OR (scope = 'market' AND market = ?))
                ORDER BY CASE scope WHEN 'symbol' THEN 1 WHEN 'industry' THEN 2 ELSE 3 END,
                         label, logical_series_id
                """,
                (cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, symbol.strip().upper(), industry, market),
            ).fetchall()
        grouped = {scope.value: [] for scope in PEScope}
        for row in rows:
            grouped[row["scope"]].append(_row_dict(row))
        return grouped
