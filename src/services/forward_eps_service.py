"""Application service for Forward EPS ingestion and v2 valuation."""

from __future__ import annotations

import hashlib
import sqlite3

from src.domain.valuation import (
    ApprovalResourceType,
    ApprovalStatus,
    ForwardEPSObservation,
    PEScenario,
    ValuationApproval,
)
from src.engine.forward_pe_valuation import ForwardPEValuationEngine
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.services.rule_registry import RuleRegistry


class ForwardEPSService:
    def __init__(self, db_path: str = "data/cache.db", *, auto_migrate: bool = True):
        self.repository = ForwardEPSRepository(db_path, auto_migrate=auto_migrate)
        self.rule_registry = RuleRegistry()
        self.engine = ForwardPEValuationEngine(
            self.repository, rule_registry=self.rule_registry
        )

    def ingest_forward_eps(
        self, observation: ForwardEPSObservation, idempotency_key: str
    ) -> dict:
        return self.repository.add_forward_eps(observation, idempotency_key)

    def ingest_pe_scenario(self, scenario: PEScenario, idempotency_key: str) -> dict:
        return self.repository.add_pe_scenario(scenario, idempotency_key)

    def record_approval(
        self,
        *,
        resource_type: ApprovalResourceType,
        resource_id: str,
        decision: ApprovalStatus,
        rule_id: str,
        rationale: str,
        available_at: str,
        approved_by: str,
        idempotency_key: str,
    ) -> dict:
        allowed_rule_ids = {
            ApprovalResourceType.FORWARD_EPS: {"VAL-02"},
            ApprovalResourceType.PE_SCENARIO: {"VAL-04"},
        }
        if rule_id not in allowed_rule_ids[resource_type]:
            raise ValueError(
                f"{rule_id} cannot approve resource type {resource_type.value}"
            )
        rule = self.rule_registry.describe(rule_id)
        identity = "|".join([
            resource_type.value,
            resource_id,
            rule_id,
            idempotency_key,
        ])
        approval_id = f"approval_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
        approval = ValuationApproval(
            approval_id=approval_id,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            rule_id=rule_id,
            evidence_level=rule["evidence_level"],
            project_operationalization=rule["project_operationalization"],
            approved_by=approved_by,
            rationale=rationale,
            available_at=available_at,
        )
        return self.repository.add_approval(approval, idempotency_key)

    def analyze(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        industry: str | None = None,
        market: str | None = None,
    ) -> dict:
        return self.engine.evaluate(
            symbol,
            knowledge_cutoff_at,
            industry=industry,
            market=market,
        )

    def analyze_preloaded(
        self,
        connection: sqlite3.Connection,
        symbol: str,
        knowledge_cutoff_at: str,
        industry: str | None = None,
        market: str | None = None,
    ) -> dict:
        """Evaluate valuation evidence on a caller-owned read transaction."""
        return self.engine.evaluate(
            symbol,
            knowledge_cutoff_at,
            industry=industry,
            market=market,
            connection=connection,
        )
