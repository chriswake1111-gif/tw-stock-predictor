"""Stored-state provider health and immutable snapshot dependency freshness."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.domain.data_foundation import (
    SnapshotFreshnessResult,
    SnapshotFreshnessStatus,
)
from src.domain.valuation import normalize_utc_timestamp
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.migration_runner import apply_valuation_migration


@dataclass(frozen=True)
class _DependencyConfig:
    table: str
    logical_field: str
    status_field: str | None
    allowed_status: str | None
    approval_table: str | None = None
    approval_link_field: str | None = None
    approval_time_field: str | None = None
    approval_resource_type: str | None = None
    approval_rule_ids: tuple[str, ...] = ()
    revision_field: str = "revision_number"


DEPENDENCIES = {
    "forward_eps_revision": _DependencyConfig(
        "forward_eps_observations", "logical_series_id", "status", "active",
        "valuation_approvals", "resource_id", "available_at", "forward_eps",
        ("VAL-02",),
    ),
    "pe_scenario_revision": _DependencyConfig(
        "pe_scenarios", "logical_series_id", None, None,
        "valuation_approvals", "resource_id", "available_at", "pe_scenario",
        ("VAL-04",),
    ),
    "anchor_revision": _DependencyConfig(
        "technical_anchor_revisions", "logical_anchor_set_id", "status", "available",
        "technical_anchor_approvals", "anchor_revision_id", "approved_at",
        None, ("FB-03", "FB-04"),
    ),
    "deployment_plan_revision": _DependencyConfig(
        "deployment_plan_revisions", "logical_campaign_id", "status", "available",
        "deployment_plan_approvals", "plan_revision_id", "approved_at",
        None, ("ENT-02",),
    ),
    "synthesis_profile_revision": _DependencyConfig(
        "synthesis_profile_revisions", "logical_profile_id", "status", "available",
        "synthesis_profile_approvals", "profile_revision_id", "approved_at",
        None, ("TGT-01",),
    ),
    "screening_profile_revision": _DependencyConfig(
        "screening_profile_revisions", "logical_profile_id", "status", "available",
        "screening_profile_approvals", "profile_revision_id", "approved_at",
        None, ("SEL-01",),
    ),
    "security_valuation_revision": _DependencyConfig(
        "security_valuation_observations", "logical_observation_id", "status", "available",
    ),
    "market_turnover_revision": _DependencyConfig(
        "market_turnover_daily", "trade_date", "status", "available",
        revision_field="revision",
    ),
    "m1b_revision": _DependencyConfig(
        "cbc_m1b_monthly", "period", "status", "available",
        revision_field="revision",
    ),
}


class DataFreshnessService:
    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        apply_valuation_migration(db_path)
        self.foundation = DataFoundationRepository(db_path)
        self.snapshots = AnalysisSnapshotRepository(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def provider_health(
        self,
        knowledge_cutoff_at: str,
        *,
        provider_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = normalize_utc_timestamp(
            knowledge_cutoff_at, "knowledge_cutoff_at"
        )
        rows = self.foundation.provider_health_as_of(
            cutoff, provider_id=provider_id, resource_id=resource_id
        )
        cutoff_trade_date = datetime.fromisoformat(
            cutoff.replace("Z", "+00:00")
        ).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
        results = []
        blocking = {
            "provider_error", "schema_changed", "rejected",
            "partial", "awaiting_review",
        }
        for row in rows:
            operational = row["operational_status"] or "unknown"
            if operational in blocking:
                freshness = "blocked"
                freshness_reason = f"latest_operational_status_{operational}"
            elif not row["last_attempt_at"]:
                freshness = "unknown"
                freshness_reason = "no_ingestion_attempt"
            elif not row["last_eligible_revision_at"]:
                freshness = "unknown"
                freshness_reason = "no_eligible_revision"
            elif row["expected_frequency"] == "daily":
                expected = row["latest_expected_trade_date"]
                actual = row["last_eligible_logical_key"]
                if not expected:
                    freshness = "unknown"
                    freshness_reason = "official_calendar_session_unavailable"
                elif not actual or actual < expected:
                    freshness = "stale"
                    freshness_reason = "newer_official_session_expected"
                elif actual == cutoff_trade_date or expected == cutoff_trade_date:
                    freshness = "current"
                    freshness_reason = None
                else:
                    freshness = "unknown"
                    freshness_reason = "official_calendar_coverage_incomplete"
            else:
                freshness = "current"
                freshness_reason = None
            results.append({
                "provider_id": row["provider_id"],
                "provider_name": row["display_name"],
                "authority_tier": row["authority_tier"],
                "provider_type": row["provider_type"],
                "resource_id": row["resource_id"],
                "resource_type": row["resource_type"],
                "expected_frequency": row["expected_frequency"],
                "last_attempt_at": row["last_attempt_at"],
                "last_success_at": row["last_success_at"],
                "last_eligible_revision_at": row["last_eligible_revision_at"],
                "status": operational,
                "freshness": freshness,
                "freshness_reason": freshness_reason,
                "latest_error": (
                    row["latest_error"] if operational in blocking else None
                ),
                "schema_version": row["schema_version"],
                "parser_version": row["parser_version"],
                "knowledge_cutoff_at": cutoff,
            })
        return results

    @staticmethod
    def _approval_for(
        conn: sqlite3.Connection,
        config: _DependencyConfig,
        resource_id: str,
        cutoff: str,
    ) -> dict[str, Any] | None:
        if not config.approval_table:
            return None
        resource_clause = ""
        params: list[Any] = [resource_id, cutoff, cutoff]
        if config.approval_resource_type:
            resource_clause = "AND resource_type = ?"
            params.append(config.approval_resource_type)
        row = conn.execute(
            f"""
            SELECT * FROM {config.approval_table}
            WHERE {config.approval_link_field} = ?
              AND {config.approval_time_field} <= ? AND ingested_at <= ?
              {resource_clause}
            ORDER BY {config.approval_time_field} DESC, ingested_at DESC,
                     approval_event_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _approval_is_eligible(
        approval: dict[str, Any] | None, config: _DependencyConfig
    ) -> bool:
        if not approval or approval.get("decision") != "approved":
            return False
        if config.approval_rule_ids and approval.get("rule_id") not in config.approval_rule_ids:
            return False
        evidence = approval.get("evidence_level")
        return evidence != "U" and (
            evidence != "C" or int(approval.get("project_operationalization", 0)) == 1
        )

    def _dependency_state(
        self,
        conn: sqlite3.Connection,
        dependency: dict[str, Any],
        cutoff: str,
    ) -> tuple[dict[str, Any], str | None, bool]:
        resource_type = str(dependency.get("resource_type", ""))
        resource_id = str(dependency.get("resource_id", ""))
        config = DEPENDENCIES.get(resource_type)
        checked = {
            "section": dependency.get("section"),
            "resource_type": resource_type,
            "snapshot_resource_id": resource_id,
            "snapshot_revision_number": dependency.get("revision_number"),
            "latest_eligible_resource_id": None,
            "latest_eligible_revision_number": None,
            "candidate_awaiting_review": False,
            "status": "unknown",
        }
        if config is None:
            return checked, f"unsupported_dependency_type_{resource_type or 'missing'}", False
        exact = conn.execute(
            f"SELECT * FROM {config.table} WHERE id = ?", (resource_id,)
        ).fetchone()
        if exact is None:
            checked["status"] = "blocked"
            return checked, "snapshot_dependency_missing", True
        exact = dict(exact)
        if (
            config.status_field
            and exact[config.status_field] != config.allowed_status
        ):
            checked["status"] = "blocked"
            return checked, "dependency_revoked", True
        logical_value = exact[config.logical_field]
        checked["logical_resource_id"] = logical_value
        rows = conn.execute(
            f"""
            SELECT * FROM {config.table}
            WHERE {config.logical_field} = ?
              AND available_at <= ? AND ingested_at <= ?
            ORDER BY {config.revision_field} DESC, available_at DESC,
                     ingested_at DESC, id DESC
            """,
            (logical_value, cutoff, cutoff),
        ).fetchall()
        visible = [dict(row) for row in rows]
        latest_visible = visible[0] if visible else None
        if (
            latest_visible
            and config.status_field
            and latest_visible[config.status_field] != config.allowed_status
        ):
            checked["status"] = "blocked"
            checked["latest_visible_resource_id"] = latest_visible["id"]
            return checked, "dependency_revoked", True
        exact_approval = self._approval_for(conn, config, resource_id, cutoff)
        if exact_approval and exact_approval["decision"] == "revoked":
            checked["status"] = "blocked"
            checked["effective_approval_status"] = "revoked"
            return checked, "approval_revoked", True

        eligible = []
        for row in visible:
            if config.status_field and row[config.status_field] != config.allowed_status:
                continue
            if config.table == "pe_scenarios":
                if row["effective_from"] and row["effective_from"] > cutoff:
                    continue
                if row["effective_to"] and row["effective_to"] <= cutoff:
                    continue
            if config.approval_table:
                if not self._approval_is_eligible(
                    self._approval_for(conn, config, row["id"], cutoff), config
                ):
                    continue
            eligible.append(row)
        latest_eligible = eligible[0] if eligible else None
        if latest_eligible:
            checked["latest_eligible_resource_id"] = latest_eligible["id"]
            checked["latest_eligible_revision_number"] = latest_eligible[
                config.revision_field
            ]
        checked["candidate_awaiting_review"] = bool(
            config.approval_table
            and latest_visible
            and (not latest_eligible or latest_visible["id"] != latest_eligible["id"])
            and not self._approval_is_eligible(
                self._approval_for(conn, config, latest_visible["id"], cutoff), config
            )
        )
        if latest_eligible is None:
            checked["status"] = "unknown"
            return checked, "no_eligible_dependency_revision", False
        if latest_eligible["id"] != resource_id:
            checked["status"] = "stale"
            return checked, f"newer_eligible_{resource_type}", False
        checked["status"] = "current"
        return checked, None, False

    def snapshot_dependency_freshness(
        self,
        snapshot_id: str,
        comparison_cutoff: str,
        *,
        checked_at: str | None = None,
    ) -> dict[str, Any] | None:
        cutoff = normalize_utc_timestamp(
            comparison_cutoff, "comparison_cutoff"
        )
        snapshot = self.snapshots.get(snapshot_id)
        if snapshot is None:
            return None
        checked_dependencies = []
        reasons = []
        blocked = False
        if not snapshot["source_resource_versions"]:
            reasons.append("snapshot_has_no_dependencies")
        with self._connect() as conn:
            for dependency in snapshot["source_resource_versions"]:
                checked, reason, is_blocked = self._dependency_state(
                    conn, dependency, cutoff
                )
                checked_dependencies.append(checked)
                if reason:
                    reasons.append(reason)
                blocked = blocked or is_blocked
        if blocked:
            status = SnapshotFreshnessStatus.BLOCKED
        elif any(item["status"] == "stale" for item in checked_dependencies):
            status = SnapshotFreshnessStatus.STALE
        elif not checked_dependencies or any(
            item["status"] == "unknown" for item in checked_dependencies
        ):
            status = SnapshotFreshnessStatus.UNKNOWN
        else:
            status = SnapshotFreshnessStatus.CURRENT
        result = SnapshotFreshnessResult(
            snapshot_id=snapshot_id,
            comparison_cutoff=cutoff,
            checked_at=checked_at or cutoff,
            freshness_status=status,
            reasons=tuple(reasons),
            checked_dependencies=tuple(checked_dependencies),
        ).canonical_payload()
        result["snapshot_output_sha256"] = snapshot["output_sha256"]
        result["historical_snapshot_validity"] = "unchanged"
        return result
