"""Pure deterministic Phase 6 percentile screening calculations."""

from __future__ import annotations

import math
from typing import Any, Protocol


class TechnicalTurnComponent(Protocol):
    """Replaceable technical confirmation boundary; never a trade signal."""

    def evaluate(
        self, symbol: str, knowledge_cutoff_at: str, profile: dict[str, Any]
    ) -> dict[str, Any]: ...


class UnavailableTechnicalTurnComponent:
    def evaluate(
        self, symbol: str, knowledge_cutoff_at: str, profile: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "status": "insufficient_data",
            "reason": "technical_confirmation_required",
            "turned_positive": None,
            "component_name": profile["technical_component"],
            "rule_id": None,
            "rule_version": None,
            "evidence_level": None,
            "implementation_mode": None,
            "project_operationalization": None,
            "data_as_of": knowledge_cutoff_at,
            "details": {},
        }


class ValueScreeningEngine:
    FORBIDDEN_TECHNICAL_RULE_IDS = {
        "MA-01",
        "MA-02",
        "MA-03",
        "SEL-02",
        "SEL-03",
    }

    @staticmethod
    def percentile(current: float, history: list[float]) -> float:
        """Upper-rank empirical percentile: ties count as less-than-or-equal."""
        return sum(value <= current for value in history) / len(history) * 100.0

    @staticmethod
    def _valid_metric(value: Any, *, allow_zero: bool) -> bool:
        if value is None:
            return False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number) and (number >= 0 if allow_zero else number > 0)

    def _metric_component(
        self,
        *,
        name: str,
        field: str,
        observations: list[dict[str, Any]],
        threshold: float,
        lower_is_better: bool,
        minimum_observations: int,
        basis: str,
        window_start: str,
        window_end: str,
    ) -> dict[str, Any]:
        current_row = observations[-1] if observations else None
        current = current_row[field] if current_row else None
        allow_zero = name == "dividend_yield"
        valid_history = [
            float(row[field])
            for row in observations
            if self._valid_metric(row[field], allow_zero=allow_zero)
        ]
        base = {
            "value": current,
            "unit": "ratio" if name == "dividend_yield" else "unitless",
            "percentile": None,
            "threshold_percentile": threshold,
            "basis": basis,
            "window_start": window_start,
            "window_end": window_end,
            "observation_count": len(valid_history),
            "minimum_observations": minimum_observations,
            "current_observation_id": current_row["id"] if current_row else None,
            "metric_date": current_row["metric_date"] if current_row else None,
            "invalid_observation_count": len(observations) - len(valid_history),
            "passed": False,
        }
        if current_row is None:
            return {**base, "status": "insufficient_data", "reason": f"{name}_history_missing"}
        if not self._valid_metric(current, allow_zero=allow_zero):
            return {**base, "status": "invalid_metric", "reason": f"invalid_{name}"}
        if len(valid_history) < minimum_observations:
            return {
                **base,
                "status": "insufficient_data",
                "reason": f"{name}_minimum_observations_not_met",
            }
        rank = self.percentile(float(current), valid_history)
        passed = rank <= threshold if lower_is_better else rank >= threshold
        return {
            **base,
            "status": "available",
            "reason": None,
            "percentile": rank,
            "passed": passed,
        }

    @staticmethod
    def forward_eps_growth_component(
        observations: list[dict[str, Any]], profile: dict[str, Any]
    ) -> dict[str, Any]:
        matching = [
            row
            for row in observations
            if row["source_name"] == profile["forward_eps_source_name"]
            and row["source_type"] == profile["forward_eps_source_type"]
        ]
        base = {
            "status": "insufficient_data",
            "reason": None,
            "source_name": profile["forward_eps_source_name"],
            "source_type": profile["forward_eps_source_type"],
            "convention": profile["forward_eps_growth_convention"],
            "earlier_fiscal_year": None,
            "later_fiscal_year": None,
            "earlier_eps_base": None,
            "later_eps_base": None,
            "growth_ratio": None,
            "observation_ids": [],
            "approval_ids": [],
            "passed": False,
        }
        by_year: dict[int, list[dict[str, Any]]] = {}
        for row in matching:
            by_year.setdefault(int(row["fiscal_year"]), []).append(row)
        if len(by_year) < 2:
            return {**base, "reason": "forward_eps_growth_requires_two_years"}
        years = sorted(by_year)
        earlier_year, later_year = years[-2:]
        selected = by_year[earlier_year] + by_year[later_year]
        if len(by_year[earlier_year]) != 1 or len(by_year[later_year]) != 1:
            return {
                **base,
                "earlier_fiscal_year": earlier_year,
                "later_fiscal_year": later_year,
                "reason": "forward_eps_source_selection_ambiguous",
            }
        earlier = by_year[earlier_year][0]
        later = by_year[later_year][0]
        details = {
            **base,
            "earlier_fiscal_year": earlier_year,
            "later_fiscal_year": later_year,
            "earlier_eps_base": earlier["eps_base"],
            "later_eps_base": later["eps_base"],
            "observation_ids": [earlier["id"], later["id"]],
            "approval_ids": [
                earlier["verified_approval_id"], later["verified_approval_id"]
            ],
        }
        if later_year != earlier_year + 1:
            return {
                **details,
                "reason": "forward_eps_growth_requires_consecutive_years",
            }
        earlier_eps = float(earlier["eps_base"])
        later_eps = float(later["eps_base"])
        if not math.isfinite(earlier_eps) or earlier_eps <= 0 or not math.isfinite(later_eps):
            return {**details, "reason": "non_comparable_forward_eps"}
        growth = (later_eps - earlier_eps) / earlier_eps
        return {
            **details,
            "status": "available",
            "reason": None,
            "growth_ratio": growth,
            "passed": growth >= 0,
        }

    def _technical_component(
        self, technical_result: dict[str, Any], profile: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(technical_result)
        result.setdefault("passed", False)
        if result.get("status") != "available":
            result["passed"] = False
            return result
        if result.get("component_name") != profile["technical_component"]:
            return {
                **result,
                "status": "insufficient_data",
                "reason": "technical_component_mismatch",
                "passed": False,
            }
        if (
            result.get("rule_id") in self.FORBIDDEN_TECHNICAL_RULE_IDS
            or result.get("evidence_level") == "U"
        ):
            return {
                **result,
                "status": "unsupported",
                "reason": "legacy_or_unsupported_technical_component",
                "passed": False,
            }
        result["passed"] = result.get("turned_positive") is True
        result.setdefault("reason", None)
        return result

    def evaluate(
        self,
        *,
        symbol: str,
        knowledge_cutoff_at: str,
        profile: dict[str, Any],
        valuation_observations: list[dict[str, Any]],
        forward_eps_observations: list[dict[str, Any]],
        technical_result: dict[str, Any],
        window_start: str,
        window_end: str,
    ) -> dict[str, Any]:
        ordered = sorted(
            valuation_observations,
            key=lambda row: (row["metric_date"], row["logical_observation_id"]),
        )
        minimum = int(profile["minimum_observations"])
        basis = profile["valuation_basis"]
        components = {
            "pe": self._metric_component(
                name="pe",
                field="pe",
                observations=ordered,
                threshold=float(profile["pe_percentile_max"]),
                lower_is_better=True,
                minimum_observations=minimum,
                basis=basis,
                window_start=window_start,
                window_end=window_end,
            ),
            "pb": self._metric_component(
                name="pb",
                field="pb",
                observations=ordered,
                threshold=float(profile["pb_percentile_max"]),
                lower_is_better=True,
                minimum_observations=minimum,
                basis=basis,
                window_start=window_start,
                window_end=window_end,
            ),
            "dividend_yield": self._metric_component(
                name="dividend_yield",
                field="dividend_yield_ratio",
                observations=ordered,
                threshold=float(profile["dividend_yield_percentile_min"]),
                lower_is_better=False,
                minimum_observations=minimum,
                basis=basis,
                window_start=window_start,
                window_end=window_end,
            ),
            "forward_eps_growth": self.forward_eps_growth_component(
                forward_eps_observations, profile
            ),
            "technical_turn": self._technical_component(technical_result, profile),
        }
        missing = [
            name
            for name, component in components.items()
            if component.get("status")
            in {"insufficient_data", "needs_human_input", "unsupported"}
        ]
        if missing:
            reason = (
                components["technical_turn"].get("reason")
                if missing == ["technical_turn"]
                else "required_screening_components_missing"
            )
            return {
                "status": "insufficient_data",
                "reason": reason,
                "research_result": None,
                "symbol": symbol,
                "knowledge_cutoff_at": knowledge_cutoff_at,
                "components": components,
                "missing_components": missing,
                "automatic_order": False,
            }
        passed = all(component.get("passed") is True for component in components.values())
        return {
            "status": "available",
            "reason": None,
            "research_result": (
                "meets_approved_profile"
                if passed
                else "does_not_meet_approved_profile"
            ),
            "symbol": symbol,
            "knowledge_cutoff_at": knowledge_cutoff_at,
            "components": components,
            "missing_components": [],
            "automatic_order": False,
        }
