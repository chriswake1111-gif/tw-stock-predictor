"""Phase 6 screening orchestration with explicit profile selection."""

from __future__ import annotations

import calendar
import hashlib
from datetime import date
from typing import Any

from src.domain.screening import (
    ScreeningProfileApproval,
    ScreeningProfileRevision,
    SecurityValuationObservation,
)
from src.domain.valuation import ApprovalStatus, normalize_utc_timestamp
from src.engine.value_screening import (
    TechnicalTurnComponent,
    UnavailableTechnicalTurnComponent,
    ValueScreeningEngine,
)
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.repositories.screening_repository import ScreeningRepository
from src.repositories.security_valuation_repository import SecurityValuationRepository
from src.services.rule_registry import RuleRegistry


def _subtract_years(value: date, years: int) -> date:
    target_year = value.year - years
    target_day = min(value.day, calendar.monthrange(target_year, value.month)[1])
    return value.replace(year=target_year, day=target_day)


class SecurityScreeningService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        *,
        technical_component: TechnicalTurnComponent | None = None,
    ):
        self.valuation_repository = SecurityValuationRepository(db_path)
        self.screening_repository = ScreeningRepository(db_path)
        self.forward_eps_repository = ForwardEPSRepository(db_path)
        self.rule_registry = RuleRegistry()
        self.engine = ValueScreeningEngine(self.rule_registry)
        self.technical_component = technical_component or UnavailableTechnicalTurnComponent()

    def ingest_valuation(
        self, observation: SecurityValuationObservation, idempotency_key: str
    ) -> dict[str, Any]:
        return self.valuation_repository.add_observation(observation, idempotency_key)

    def ingest_profile(
        self, revision: ScreeningProfileRevision, idempotency_key: str
    ) -> dict[str, Any]:
        return self.screening_repository.add_revision(revision, idempotency_key)

    def record_profile_approval(
        self,
        *,
        profile_revision_id: str,
        decision: ApprovalStatus,
        rationale: str,
        approved_at: str,
        approved_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        rule = self.rule_registry.describe("SEL-01")
        if not rule["human_approval_required"]:
            raise ValueError("SEL-01 approval governance is misconfigured")
        identity = "|".join(
            [profile_revision_id, "SEL-01", idempotency_key]
        )
        approval = ScreeningProfileApproval(
            approval_id=(
                "screening_approval_"
                f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
            ),
            profile_revision_id=profile_revision_id,
            decision=decision,
            rule_id="SEL-01",
            rule_version=rule["version"],
            evidence_level=rule["evidence_level"],
            implementation_mode=rule["implementation_mode"],
            project_operationalization=rule["project_operationalization"],
            approved_by=approved_by,
            rationale=rationale,
            approved_at=approved_at,
        )
        return self.screening_repository.add_approval(approval, idempotency_key)

    @staticmethod
    def _approved(profile: dict[str, Any]) -> bool:
        return (
            profile["status"] == "available"
            and profile.get("effective_approval_status") == "approved"
            and profile.get("approval_rule_id") == "SEL-01"
            and profile.get("approved_evidence_level") == "C"
            and profile.get("approved_implementation_mode")
            == "project_operationalization"
            and profile.get("project_operationalization") == 1
        )

    @staticmethod
    def _scope_applies(profile: dict[str, Any], symbol: str) -> bool:
        if profile["scope"] == "global":
            return True
        if profile["scope"] == "symbol":
            return profile["scope_value"] == symbol
        return False

    def _select_profile(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        *,
        logical_profile_id: str | None,
        profile_revision_id: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        states = self.screening_repository.effective_profile_states_as_of(
            knowledge_cutoff_at
        )
        if logical_profile_id or profile_revision_id:
            selected = [
                state
                for state in states
                if (logical_profile_id is None or state["logical_profile_id"] == logical_profile_id)
                and (profile_revision_id is None or state["id"] == profile_revision_id)
            ]
            if len(selected) != 1:
                return None, {
                    "status": "needs_human_input",
                    "reason": "approved_screening_profile_required",
                }
            profile = selected[0]
            if profile["status"] == "revoked":
                return None, {
                    "status": "needs_human_input",
                    "reason": "screening_profile_revoked",
                    "profile_revision_id": profile["id"],
                }
            if profile.get("effective_approval_status") == "revoked":
                return None, {
                    "status": "needs_human_input",
                    "reason": "screening_profile_approval_revoked",
                    "profile_revision_id": profile["id"],
                }
            if not self._approved(profile):
                return None, {
                    "status": "needs_human_input",
                    "reason": "approved_screening_profile_required",
                    "profile_revision_id": profile["id"],
                }
            if not self._scope_applies(profile, symbol):
                reason = (
                    "industry_peer_data_required"
                    if profile["scope"] == "industry"
                    else "screening_profile_scope_mismatch"
                )
                return None, {"status": "insufficient_data", "reason": reason}
            return profile, None

        applicable = [
            state
            for state in states
            if self._approved(state) and self._scope_applies(state, symbol)
        ]
        if not applicable:
            return None, {
                "status": "needs_human_input",
                "reason": "approved_screening_profile_required",
            }
        if len(applicable) > 1:
            return None, {
                "status": "needs_human_input",
                "reason": "screening_profile_selection_required",
                "applicable_profile_revision_ids": [row["id"] for row in applicable],
            }
        return applicable[0], None

    def analyze(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        *,
        logical_profile_id: str | None = None,
        profile_revision_id: str | None = None,
    ) -> dict[str, Any]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        symbol = symbol.strip().upper()
        profile, selection_error = self._select_profile(
            symbol,
            cutoff,
            logical_profile_id=logical_profile_id,
            profile_revision_id=profile_revision_id,
        )
        if selection_error:
            return {
                **selection_error,
                "symbol": symbol,
                "knowledge_cutoff_at": cutoff,
                "research_result": None,
                "components": {},
                "rules_used": [],
                "automatic_order": False,
            }
        assert profile is not None
        if profile["valuation_basis"] == "industry" or profile["scope"] == "industry":
            return {
                "status": "insufficient_data",
                "reason": "industry_peer_data_required",
                "symbol": symbol,
                "knowledge_cutoff_at": cutoff,
                "research_result": None,
                "components": {},
                "rules_used": [],
                "automatic_order": False,
            }
        cutoff_date = date.fromisoformat(cutoff[:10])
        window_start = _subtract_years(
            cutoff_date, int(profile["history_years"])
        ).isoformat()
        window_end = cutoff_date.isoformat()
        valuations = self.valuation_repository.observations_as_of(
            symbol,
            cutoff,
            source_name=profile["valuation_source_name"],
            source_dataset=profile["valuation_source_dataset"],
            window_start=window_start,
            window_end=window_end,
        )
        forward_eps = self.forward_eps_repository.forward_eps_as_of(symbol, cutoff)
        technical_result = self.technical_component.evaluate(symbol, cutoff, profile)
        result = self.engine.evaluate(
            symbol=symbol,
            knowledge_cutoff_at=cutoff,
            profile=profile,
            valuation_observations=valuations,
            forward_eps_observations=forward_eps,
            technical_result=technical_result,
            window_start=window_start,
            window_end=window_end,
        )
        profile_trace = {
            "rule_id": "SEL-01",
            "version": profile["approval_rule_version"],
            "evidence_level": "C",
            "implementation_mode": "project_operationalization",
            "project_operationalization": True,
            "logical_profile_id": profile["logical_profile_id"],
            "profile_revision_id": profile["id"],
            "approval_id": profile["verified_approval_id"],
            "source_data_as_of": cutoff,
        }
        technical_trace = None
        canonical_technical = result.get("components", {}).get("technical_turn", {})
        if (
            canonical_technical.get("status") == "available"
            and canonical_technical.get("rule_id")
        ):
            technical_trace = {
                "rule_id": canonical_technical["rule_id"],
                "version": canonical_technical["rule_version"],
                "evidence_level": canonical_technical["evidence_level"],
                "implementation_mode": canonical_technical["implementation_mode"],
                "project_operationalization": canonical_technical.get(
                    "project_operationalization", False
                ),
                "source_data_as_of": canonical_technical.get("data_as_of", cutoff),
            }
        result["profile"] = {
            "logical_profile_id": profile["logical_profile_id"],
            "profile_revision_id": profile["id"],
            "approval_id": profile["verified_approval_id"],
            "scope": profile["scope"],
            "scope_value": profile["scope_value"],
        }
        result["rule_trace"] = profile_trace if result["status"] == "available" else None
        result["technical_rule_trace"] = technical_trace
        result["rules_used"] = (
            [profile_trace] + ([technical_trace] if technical_trace else [])
            if result["status"] == "available"
            else []
        )
        result["limitations"] = [
            "research_screening_only",
            "not_buy_or_sell_signal",
            "no_automatic_order",
            "percentile_thresholds_are_project_operationalization",
        ]
        result["source_resource_versions"] = [
            {
                "section": "screening",
                "resource_type": "screening_profile_revision",
                "resource_id": profile["id"],
                "logical_resource_id": profile["logical_profile_id"],
                "revision_number": profile["revision_number"],
                "available_at": profile["available_at"],
                "ingested_at": profile["ingested_at"],
                "approval_ids": [profile["verified_approval_id"]],
            },
            *[
                {
                    "section": "screening",
                    "resource_type": "security_valuation_revision",
                    "resource_id": row["id"],
                    "logical_resource_id": row["logical_observation_id"],
                    "revision_number": row["revision_number"],
                    "available_at": row["available_at"],
                    "ingested_at": row["ingested_at"],
                    "approval_ids": [],
                }
                for row in valuations
            ],
            *[
                {
                    "section": "screening",
                    "resource_type": "forward_eps_revision",
                    "resource_id": row["id"],
                    "logical_resource_id": row["logical_series_id"],
                    "revision_number": row["revision_number"],
                    "available_at": row["available_at"],
                    "ingested_at": row["ingested_at"],
                    "approval_ids": [row["verified_approval_id"]],
                }
                for row in forward_eps
            ],
        ]
        return result
