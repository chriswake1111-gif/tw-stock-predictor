"""Trusted Phase 2/4 adapters and TGT-01 profile-governed synthesis."""

from __future__ import annotations

import hashlib
from typing import Any

from src.domain.analysis_snapshot import (
    AnalysisSnapshot,
    CaptureMode,
    SynthesisProfileApproval,
    SynthesisProfileRevision,
)
from src.domain.valuation import ApprovalStatus, utc_now_timestamp
from src.domain.valuation import normalize_utc_timestamp
from src.engine.target_confluence import TargetConfluenceEngine
from src.repositories.analysis_snapshot_repository import AnalysisSnapshotRepository
from src.repositories.synthesis_profile_repository import SynthesisProfileRepository
from src.services.rule_registry import RuleRegistry


class EvidenceAnalysisService:
    def __init__(self, db_path: str = "data/cache.db"):
        self.profile_repository = SynthesisProfileRepository(db_path)
        self.snapshot_repository = AnalysisSnapshotRepository(db_path)
        self.registry = RuleRegistry()
        self.engine = TargetConfluenceEngine()

    def ingest_profile(
        self, revision: SynthesisProfileRevision, idempotency_key: str
    ) -> dict[str, Any]:
        return self.profile_repository.add_revision(revision, idempotency_key)

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
        rule = self.registry.describe("TGT-01")
        identity = "|".join([profile_revision_id, "TGT-01", idempotency_key])
        approval = SynthesisProfileApproval(
            approval_id=(
                "synthesis_approval_"
                f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
            ),
            profile_revision_id=profile_revision_id,
            decision=decision,
            rule_id="TGT-01",
            rule_version=rule["version"],
            evidence_level=rule["evidence_level"],
            implementation_mode=rule["implementation_mode"],
            project_operationalization=rule["project_operationalization"],
            approved_by=approved_by,
            rationale=rationale,
            approved_at=approved_at,
        )
        return self.profile_repository.add_approval(approval, idempotency_key)

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
            "synthesis_profile_logical_id": profile["logical_profile_id"],
            "synthesis_profile_revision_number": profile["revision_number"],
            "synthesis_profile_available_at": profile["available_at"],
            "synthesis_profile_ingested_at": profile["ingested_at"],
            "automatic_order": False,
        })
        return result

    @staticmethod
    def _snapshot_provenance(analysis: dict[str, Any]) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        valuation = analysis.get("valuation", {})
        for resource_type, rows in (
            ("forward_eps_revision", valuation.get("forward_eps", [])),
            ("pe_scenario_revision", valuation.get("pe_scenarios", [])),
        ):
            for row in rows:
                if not row.get("id"):
                    continue
                approval_id = row.get("verified_approval_id")
                resources.append({
                    "section": "valuation",
                    "resource_type": resource_type,
                    "resource_id": row["id"],
                    "logical_resource_id": row.get("logical_series_id"),
                    "revision_number": row.get("revision_number"),
                    "available_at": row.get("available_at"),
                    "ingested_at": row.get("ingested_at"),
                    "approval_ids": [approval_id] if approval_id else [],
                })
        for scenario in analysis.get("technical_support", {}).get("scenarios", []):
            trace = scenario.get("rule_trace", {})
            resources.append({
                "section": "technical_support",
                "resource_type": "anchor_revision",
                "resource_id": scenario["anchor_set_revision_id"],
                "logical_resource_id": None,
                "revision_number": scenario.get("anchor_revision_number"),
                "available_at": scenario.get("anchor_available_at"),
                "ingested_at": scenario.get("anchor_ingested_at"),
                "approval_ids": [trace["approval_id"]] if trace.get("approval_id") else [],
            })
        resources.extend(analysis.get("liquidity", {}).get("source_resource_versions", []))
        resources.extend(analysis.get("screening", {}).get("source_resource_versions", []))
        for plan in analysis.get("deployment_plan", {}).get("plans", []):
            trace = plan.get("rule_trace") or {}
            resources.append({
                "section": "deployment_plan",
                "resource_type": "deployment_plan_revision",
                "resource_id": plan["plan_revision_id"],
                "logical_resource_id": plan.get("logical_campaign_id"),
                "revision_number": plan.get("revision_number"),
                "available_at": plan.get("available_at"),
                "ingested_at": plan.get("ingested_at"),
                "approval_ids": [trace["approval_id"]] if trace.get("approval_id") else [],
            })
        confluence = analysis.get("target_confluence", {})
        if confluence.get("synthesis_profile_revision_id"):
            resources.append({
                "section": "target_confluence",
                "resource_type": "synthesis_profile_revision",
                "resource_id": confluence["synthesis_profile_revision_id"],
                "logical_resource_id": confluence.get("synthesis_profile_logical_id"),
                "revision_number": confluence.get("synthesis_profile_revision_number"),
                "available_at": confluence.get("synthesis_profile_available_at"),
                "ingested_at": confluence.get("synthesis_profile_ingested_at"),
                "approval_ids": [confluence["synthesis_profile_approval_id"]],
            })
        unique = {
            (item["section"], item["resource_type"], item["resource_id"]): item
            for item in resources
        }
        return [unique[key] for key in sorted(unique)]

    def create_snapshot(
        self,
        *,
        analysis: dict[str, Any],
        capture_mode: CaptureMode,
        idempotency_key: str,
        supersedes_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        resources = self._snapshot_provenance(analysis)
        approvals = sorted({
            approval_id
            for item in resources
            for approval_id in item.get("approval_ids", [])
        })
        used_rule_versions = {
            trace["rule_id"]: trace.get("rule_version") or trace.get("version")
            for trace in analysis.get("rules_used", [])
            if trace.get("rule_id") and (trace.get("rule_version") or trace.get("version"))
        }
        confluence = analysis.get("target_confluence", {})
        snapshot = AnalysisSnapshot(
            symbol=analysis["symbol"],
            knowledge_cutoff_at=analysis["knowledge_cutoff_at"],
            capture_mode=capture_mode,
            model_version=analysis["model"]["version"],
            synthesis_profile_revision_id=confluence.get("synthesis_profile_revision_id"),
            synthesis_profile_approval_id=confluence.get("synthesis_profile_approval_id"),
            used_rule_versions=used_rule_versions,
            source_resource_versions=resources,
            manual_approval_ids=approvals,
            output=analysis,
            created_at=utc_now_timestamp(),
            supersedes_snapshot_id=supersedes_snapshot_id,
        )
        return self.snapshot_repository.add(snapshot, idempotency_key)
