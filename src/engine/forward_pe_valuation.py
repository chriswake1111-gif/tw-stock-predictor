"""Evidence-traced Forward EPS multiplied by approved symbol PE scenarios."""

from __future__ import annotations

from typing import Any

from src.domain.valuation import normalize_utc_timestamp
from src.repositories.forward_eps_repository import ForwardEPSRepository
from src.services.rule_registry import RuleRegistry


class ForwardPEValuationEngine:
    def __init__(
        self,
        repository: ForwardEPSRepository,
        rule_registry: RuleRegistry | None = None,
    ):
        self.repository = repository
        self.rule_registry = rule_registry or RuleRegistry()

    @staticmethod
    def _public_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key not in {"idempotency_key", "payload_fingerprint", "revision_rank"}
        }

    def _rule_trace(
        self,
        rule_id: str,
        knowledge_cutoff_at: str,
        approval_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        rule = self.rule_registry.describe(rule_id)
        return {
            "rule_id": rule_id,
            "rule_version": rule["version"],
            "evidence_level": rule["evidence_level"],
            "implementation_mode": rule["implementation_mode"],
            "project_operationalization": rule["project_operationalization"],
            "source_data_as_of": knowledge_cutoff_at,
            "approval_ids": sorted(set(approval_ids or [])),
        }

    def evaluate(
        self,
        symbol: str,
        knowledge_cutoff_at: str,
        industry: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        knowledge_cutoff_at = normalize_utc_timestamp(
            knowledge_cutoff_at, "knowledge_cutoff_at"
        )
        observations = self.repository.forward_eps_as_of(symbol, knowledge_cutoff_at)
        pe_by_scope = self.repository.pe_scenarios_as_of(
            symbol, knowledge_cutoff_at, industry=industry, market=market
        )
        symbol_scenarios = pe_by_scope["symbol"]
        forecast_approval_ids = [
            row["verified_approval_id"] for row in observations
        ]
        pe_approval_ids = [
            row["verified_approval_id"] for row in symbol_scenarios
        ]
        rules_used = [
            self._rule_trace(
                "VAL-01", knowledge_cutoff_at,
                forecast_approval_ids + pe_approval_ids,
            ),
            self._rule_trace("VAL-02", knowledge_cutoff_at, forecast_approval_ids),
            self._rule_trace("VAL-03", knowledge_cutoff_at, pe_approval_ids),
            self._rule_trace("VAL-04", knowledge_cutoff_at, pe_approval_ids),
        ]
        base = {
            "knowledge_cutoff_at": knowledge_cutoff_at,
            "forward_eps": [self._public_record(row) for row in observations],
            "pe_scenarios": [self._public_record(row) for row in symbol_scenarios],
            "reference_pe_scenarios": {
                "industry": [self._public_record(row) for row in pe_by_scope["industry"]],
                "market": [self._public_record(row) for row in pe_by_scope["market"]],
                "automatic_use": False,
            },
            "target_matrix": [],
            "historical_ttm_reference": {
                "status": "insufficient_data",
                "value": None,
                "reason": "Historical TTM is not loaded into the Forward EPS matrix",
            },
            "invalidation_conditions": [
                "forward_eps_revised_or_revoked",
                "approved_symbol_pe_revised_revoked_or_expired",
            ],
            "rules_used": rules_used,
            "multiple_sources_aggregated": False,
            "official_affiliation": False,
        }
        if not observations:
            return {
                **base,
                "status": "insufficient_data",
                "reason": "forward_eps_missing_at_knowledge_cutoff",
            }
        if not symbol_scenarios:
            return {
                **base,
                "status": "needs_human_input",
                "reason": "approved_symbol_pe_missing_at_knowledge_cutoff",
            }

        cells = []
        applicable_count = 0
        for observation in observations:
            eps_values = [
                ("low", observation["eps_low"]),
                ("base", observation["eps_base"]),
                ("high", observation["eps_high"]),
            ]
            for eps_label, eps_value in eps_values:
                if eps_value is None:
                    continue
                for pe_scenario in symbol_scenarios:
                    applicable = float(eps_value) > 0
                    if applicable:
                        applicable_count += 1
                    cells.append({
                        "status": "available" if applicable else "not_applicable",
                        "observation_id": observation["id"],
                        "observation_logical_series_id": observation["logical_series_id"],
                        "observation_revision_number": observation["revision_number"],
                        "source_name": observation["source_name"],
                        "source_type": observation["source_type"],
                        "fiscal_year": observation["fiscal_year"],
                        "eps_scenario": eps_label,
                        "eps_value": float(eps_value),
                        "pe_scenario_id": pe_scenario["id"],
                        "pe_logical_series_id": pe_scenario["logical_series_id"],
                        "pe_revision_number": pe_scenario["revision_number"],
                        "pe_label": pe_scenario["label"],
                        "pe_value": float(pe_scenario["pe_value"]),
                        "target_price": round(float(eps_value) * float(pe_scenario["pe_value"]), 4) if applicable else None,
                        "formula": "forward_eps * approved_symbol_pe",
                        "rule_ids": ["VAL-01", "VAL-02", "VAL-03", "VAL-04"],
                        "approval_ids": {
                            "forward_eps": observation["verified_approval_id"],
                            "pe_scenario": pe_scenario["verified_approval_id"],
                        },
                    })
        return {
            **base,
            "status": "available" if applicable_count else "not_applicable",
            "reason": None if applicable_count else "forward_eps_is_zero_or_negative",
            "target_matrix": cells,
        }
