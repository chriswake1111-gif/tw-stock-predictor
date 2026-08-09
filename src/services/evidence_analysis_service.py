"""Trusted Phase 2/4 adapters and TGT-01 profile-governed synthesis."""

from __future__ import annotations

from typing import Any

from src.domain.valuation import normalize_utc_timestamp
from src.engine.target_confluence import TargetConfluenceEngine
from src.repositories.synthesis_profile_repository import SynthesisProfileRepository
from src.services.rule_registry import RuleRegistry


class EvidenceAnalysisService:
    def __init__(self, db_path: str = "data/cache.db"):
        self.profile_repository = SynthesisProfileRepository(db_path)
        self.registry = RuleRegistry()
        self.engine = TargetConfluenceEngine()

    @staticmethod
    def _approved(profile: dict[str, Any]) -> bool:
        return (
            profile["status"] == "available"
            and profile.get("effective_approval_status") == "approved"
            and profile.get("approval_rule_id") == "TGT-01"
            and profile.get("approved_evidence_level") == "C"
            and profile.get("approved_implementation_mode") == "project_operationalization"
            and profile.get("project_operationalization") == 1
        )

    @staticmethod
    def _scope_applies(profile: dict[str, Any], symbol: str) -> bool:
        return profile["scope"] == "global" or profile["scope_value"] == symbol

    def select_profile(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        *,
        logical_profile_id: str | None = None,
        profile_revision_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        states = self.profile_repository.effective_states_as_of(cutoff)
        if profile_revision_id:
            selected = [state for state in states if state["id"] == profile_revision_id]
            if not selected:
                requested = self.profile_repository.get_revision(profile_revision_id)
                if requested:
                    latest = [
                        state for state in states
                        if state["logical_profile_id"] == requested["logical_profile_id"]
                    ]
                    if latest and latest[0]["id"] != profile_revision_id:
                        return None, {
                            "status": "needs_human_input",
                            "reason": "synthesis_profile_revision_superseded",
                            "profile_revision_id": profile_revision_id,
                            "effective_profile_revision_id": latest[0]["id"],
                        }
                return None, {
                    "status": "needs_human_input",
                    "reason": "approved_synthesis_profile_required",
                    "profile_revision_id": profile_revision_id,
                }
            candidates = selected
        elif logical_profile_id:
            candidates = [
                state for state in states
                if state["logical_profile_id"] == logical_profile_id
            ]
        else:
            candidates = [
                state for state in states if self._scope_applies(state, symbol)
            ]
        if not candidates:
            return None, {
                "status": "needs_human_input",
                "reason": "approved_synthesis_profile_required",
            }
        if len(candidates) > 1:
            return None, {
                "status": "needs_human_input",
                "reason": "synthesis_profile_selection_required",
                "applicable_profile_revision_ids": sorted(item["id"] for item in candidates),
            }
        profile = candidates[0]
        if not self._scope_applies(profile, symbol):
            return None, {
                "status": "insufficient_data",
                "reason": "synthesis_profile_scope_mismatch",
            }
        if profile["status"] == "revoked":
            return None, {
                "status": "needs_human_input",
                "reason": "synthesis_profile_revoked",
                "profile_revision_id": profile["id"],
            }
        if profile.get("effective_approval_status") == "revoked":
            return None, {
                "status": "needs_human_input",
                "reason": "synthesis_profile_approval_revoked",
                "profile_revision_id": profile["id"],
            }
        if not self._approved(profile):
            return None, {
                "status": "needs_human_input",
                "reason": "approved_synthesis_profile_required",
                "profile_revision_id": profile["id"],
            }
        return profile, None

    def _rule_metadata(self, rule_id: str) -> dict[str, Any]:
        rule = self.registry.describe(rule_id)
        return {
            "rule_version": rule["version"],
            "evidence_level": rule["evidence_level"],
            "implementation_mode": rule["implementation_mode"],
        }

    def candidates_from_outputs(
        self,
        *,
        valuation: dict[str, Any],
        technical_support: dict[str, Any],
        knowledge_cutoff_at: str,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if valuation.get("status") in {"available", "not_applicable"}:
            for cell in valuation.get("target_matrix", []):
                if cell.get("status") != "available" or cell.get("target_price") is None:
                    continue
                candidates.append({
                    "candidate_id": (
                        f"valuation:{cell['observation_id']}:{cell['eps_scenario']}:"
                        f"{cell['pe_scenario_id']}"
                    ),
                    "method_family": "VAL-01",
                    "rule_id": "VAL-01",
                    **self._rule_metadata("VAL-01"),
                    "semantic_role": "target",
                    "price": str(cell["target_price"]),
                    "price_unit": "TWD_per_share",
                    "source_resource_ids": [
                        cell["observation_id"], cell["pe_scenario_id"]
                    ],
                    "dependency_keys": [
                        f"forward_eps_revision:{cell['observation_id']}",
                        f"pe_scenario_revision:{cell['pe_scenario_id']}",
                    ],
                    "approval_ids": sorted(cell["approval_ids"].values()),
                    "source_data_as_of": knowledge_cutoff_at,
                })
        if technical_support.get("status") == "available":
            for scenario in technical_support.get("scenarios", []):
                trace = scenario["rule_trace"]
                rule_id = trace["rule_id"]
                if rule_id not in {"FB-03", "FB-04"}:
                    continue
                candidates.append({
                    "candidate_id": (
                        f"technical:{scenario['anchor_set_revision_id']}:{rule_id}"
                    ),
                    "method_family": rule_id,
                    "rule_id": rule_id,
                    **self._rule_metadata(rule_id),
                    "semantic_role": "target" if rule_id == "FB-03" else "support",
                    "price": str(scenario["calculated_level"]),
                    "price_unit": scenario["price_unit"],
                    "source_resource_ids": [scenario["anchor_set_revision_id"]],
                    "dependency_keys": [
                        f"anchor_revision:{scenario['anchor_set_revision_id']}"
                    ],
                    "approval_ids": [trace["approval_id"]],
                    "source_data_as_of": knowledge_cutoff_at,
                })
        return candidates

    def synthesize(
        self,
        *,
        symbol: str,
        knowledge_cutoff_at: str,
        valuation: dict[str, Any],
        technical_support: dict[str, Any],
        logical_profile_id: str | None = None,
        profile_revision_id: str | None = None,
    ) -> dict[str, Any]:
        cutoff = normalize_utc_timestamp(knowledge_cutoff_at, "knowledge_cutoff_at")
        symbol = symbol.strip().upper()
        profile, error = self.select_profile(
            symbol,
            cutoff,
            logical_profile_id=logical_profile_id,
            profile_revision_id=profile_revision_id,
        )
        if error:
            return {
                **error,
                "symbol": symbol,
                "knowledge_cutoff_at": cutoff,
                "overlap_ranges": [],
                "candidate_count": 0,
                "support_count": 0,
                "independent_method_count": 0,
                "evidence_strength": None,
                "rules_used": [],
                "automatic_order": False,
            }
        assert profile is not None
        candidates = self.candidates_from_outputs(
            valuation=valuation,
            technical_support=technical_support,
            knowledge_cutoff_at=cutoff,
        )
        rule = self.registry.describe("TGT-01")
        trace = {
            "rule_id": "TGT-01",
            "rule_version": rule["version"],
            "evidence_level": rule["evidence_level"],
            "implementation_mode": rule["implementation_mode"],
            "project_operationalization": rule["project_operationalization"],
            "source_data_as_of": cutoff,
            "approval_ids": [profile["verified_approval_id"]],
            "profile_revision_id": profile["id"],
        }
        result = self.engine.evaluate(
            candidates=candidates, profile=profile, rule_trace=trace
        )
        result.update({
            "symbol": symbol,
            "knowledge_cutoff_at": cutoff,
            "automatic_order": False,
        })
        return result
