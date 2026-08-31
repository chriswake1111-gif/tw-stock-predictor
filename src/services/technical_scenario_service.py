"""As-of orchestration for approved manual-anchor price scenarios."""

from __future__ import annotations

import hashlib
import sqlite3

from src.domain.technical_anchor import ManualAnchorSetRevision, TechnicalAnchorApproval
from src.domain.valuation import ApprovalStatus
from src.engine.fibonacci_scenarios import calculate_equal_amplitude, calculate_retracement_0382
from src.repositories.technical_anchor_repository import TechnicalAnchorRepository
from src.services.rule_registry import RuleRegistry


class TechnicalScenarioService:
    def __init__(self, db_path: str = "data/cache.db", *, auto_migrate: bool = True):
        self.repository = TechnicalAnchorRepository(db_path, auto_migrate=auto_migrate)
        self.registry = RuleRegistry()

    def ingest(self, revision: ManualAnchorSetRevision, idempotency_key: str) -> dict:
        return self.repository.add_anchor_revision(revision, idempotency_key)

    def record_approval(
        self, *, anchor_revision_id: str, decision: ApprovalStatus, rule_id: str,
        rationale: str, approved_at: str, approved_by: str, idempotency_key: str,
    ) -> dict:
        rule = self.registry.describe(rule_id)
        if rule_id not in {"FB-03", "FB-04"} or not rule["human_approval_required"]:
            raise ValueError("rule is not eligible for Phase 4 anchor approval")
        identity = "|".join([anchor_revision_id, rule_id, idempotency_key])
        approval = TechnicalAnchorApproval(
            approval_id=f"anchor_approval_{hashlib.sha256(identity.encode()).hexdigest()[:24]}",
            anchor_revision_id=anchor_revision_id,
            decision=decision,
            rule_id=rule_id,
            rule_version=rule["version"],
            evidence_level=rule["evidence_level"],
            implementation_mode=rule["implementation_mode"],
            project_operationalization=rule["project_operationalization"],
            approved_by=approved_by,
            rationale=rationale,
            approved_at=approved_at,
        )
        return self.repository.add_approval(approval, idempotency_key)

    def analyze(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict:
        states = (
            self.repository.states_as_of_with_connection(
                connection, symbol, knowledge_cutoff_at
            )
            if connection is not None
            else self.repository.states_as_of(symbol, knowledge_cutoff_at)
        )
        if not states:
            return {
                "status": "needs_human_input",
                "reason": "manual_anchor_required",
                "symbol": symbol,
                "scenarios": [],
                "rules_used": [],
            }
        scenarios = []
        revoked = 0
        pending = 0
        for state in states:
            approval = state["approval"]
            if state["status"] == "revoked" or (approval and approval["decision"] == "revoked"):
                revoked += 1
                continue
            if not approval or approval["decision"] != "approved":
                pending += 1
                continue
            by_role = {anchor["role"]: anchor for anchor in state["anchors"]}
            rule_id = state["evidence_basis_rule_id"]
            if rule_id == "FB-03":
                result = calculate_equal_amplitude(
                    by_role["origin"]["price"], by_role["swing_end"]["price"],
                    by_role["projection_origin"]["price"],
                )
            elif rule_id == "FB-04":
                result = calculate_retracement_0382(
                    by_role["origin"]["price"], by_role["swing_end"]["price"]
                )
            else:
                continue
            anchor_ids = {
                role: f"{state['id']}:{role}" for role in by_role
            }
            trace = {
                "rule_id": rule_id,
                "rule_version": approval["rule_version"],
                "evidence_level": approval["evidence_level"],
                "implementation_mode": approval["implementation_mode"],
                "project_operationalization": bool(approval["project_operationalization"]),
                "source_data_as_of": knowledge_cutoff_at,
                "approval_id": approval["approval_id"],
                "anchor_revision_ids": [state["id"]],
            }
            scenarios.append({
                **result,
                "semantic_role": "target" if rule_id == "FB-03" else "support",
                "symbol": symbol,
                "anchor_set_revision_id": state["id"],
                "anchor_revision_number": state["revision_number"],
                "anchor_available_at": state["available_at"],
                "anchor_ingested_at": state["ingested_at"],
                "anchors": by_role,
                "anchor_ids": anchor_ids,
                "interpretation": "scenario_reference_only_not_guaranteed_not_trade_instruction",
                "rule_trace": trace,
            })
        if not scenarios:
            reason = "anchor_approval_revoked" if revoked and not pending else "approved_manual_anchor_required"
            return {
                "status": "needs_human_input",
                "reason": reason,
                "symbol": symbol,
                "scenarios": [],
                "rules_used": [],
            }
        return {
            "status": "available",
            "symbol": symbol,
            "knowledge_cutoff_at": knowledge_cutoff_at,
            "scenarios": scenarios,
            "pending_anchor_sets": pending,
            "revoked_anchor_sets": revoked,
            "rules_used": [scenario["rule_trace"] for scenario in scenarios],
            "limitations": ["scenario_not_prediction", "not_guaranteed_target", "not_trade_instruction"],
        }

    def analyze_preloaded(
        self, connection: sqlite3.Connection, symbol: str, knowledge_cutoff_at: str
    ) -> dict:
        """Evaluate approved anchors on a caller-owned read transaction."""
        return self.analyze(
            symbol, knowledge_cutoff_at, connection=connection
        )
