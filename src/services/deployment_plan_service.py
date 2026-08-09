"""Phase 5 deployment-plan orchestration and approval enforcement."""

from __future__ import annotations

import hashlib

from src.domain.deployment import DeploymentPlanApproval, DeploymentPlanRevision
from src.domain.valuation import ApprovalStatus
from src.repositories.deployment_plan_repository import DeploymentPlanRepository
from src.services.rule_registry import RuleRegistry
from src.strategy.three_tranche_planner import build_three_tranche_plan


class DeploymentPlanService:
    def __init__(self, db_path: str = "data/cache.db"):
        self.repository = DeploymentPlanRepository(db_path)
        self.registry = RuleRegistry()

    def ingest(self, revision: DeploymentPlanRevision, idempotency_key: str) -> dict:
        return self.repository.add_revision(revision, idempotency_key)

    def record_approval(
        self, *, plan_revision_id: str, decision: ApprovalStatus, rationale: str,
        approved_at: str, approved_by: str, idempotency_key: str,
    ) -> dict:
        rule = self.registry.describe("ENT-02")
        if not rule["human_approval_required"]:
            raise ValueError("ENT-02 approval governance is misconfigured")
        identity = "|".join([plan_revision_id, "ENT-02", idempotency_key])
        approval = DeploymentPlanApproval(
            approval_id=f"deployment_approval_{hashlib.sha256(identity.encode()).hexdigest()[:24]}",
            plan_revision_id=plan_revision_id,
            decision=decision,
            rule_id="ENT-02",
            rule_version=rule["version"],
            evidence_level=rule["evidence_level"],
            implementation_mode=rule["implementation_mode"],
            project_operationalization=rule["project_operationalization"],
            approved_by=approved_by,
            rationale=rationale,
            approved_at=approved_at,
        )
        return self.repository.add_approval(approval, idempotency_key)

    @staticmethod
    def _plan_for_state(state: dict) -> dict:
        result = build_three_tranche_plan(
            state["planned_total_capital"], state["triggers"], approval=state["approval"]
        )
        result.update({
            "plan_revision_id": state["id"],
            "logical_campaign_id": state["logical_campaign_id"],
            "revision_number": state["revision_number"],
            "symbol": state["symbol"],
            "available_at": state["available_at"],
        })
        if state["status"] == "revoked" or (
            state["approval"] and state["approval"]["decision"] == "revoked"
        ):
            result.update({
                "status": "needs_human_input",
                "reason": "deployment_plan_approval_revoked",
                "rule_trace": None,
                "pending_rule_id": "ENT-02",
            })
        return result

    def analyze(self, symbol: str, knowledge_cutoff_at: str) -> dict:
        states = self.repository.states_as_of(symbol, knowledge_cutoff_at)
        if not states:
            return {
                "status": "needs_human_input",
                "reason": "deployment_plan_request_required",
                "symbol": symbol,
                "plans": [],
                "rules_used": [],
            }
        plans = [self._plan_for_state(state) for state in states]
        for plan in plans:
            if plan["rule_trace"]:
                plan["rule_trace"]["source_data_as_of"] = knowledge_cutoff_at
        available = [plan for plan in plans if plan["status"] == "available"]
        if not available:
            reasons = {plan["reason"] for plan in plans}
            reason = (
                "deployment_plan_approval_revoked"
                if "deployment_plan_approval_revoked" in reasons
                else "deployment_triggers_required"
                if "deployment_triggers_required" in reasons
                else "approved_deployment_plan_required"
            )
            return {
                "status": "needs_human_input",
                "reason": reason,
                "symbol": symbol,
                "plans": plans,
                "rules_used": [],
            }
        return {
            "status": "available",
            "symbol": symbol,
            "knowledge_cutoff_at": knowledge_cutoff_at,
            "plans": plans,
            "rules_used": [plan["rule_trace"] for plan in available],
            "limitations": ["not_trade_instruction", "no_automatic_order"],
        }
