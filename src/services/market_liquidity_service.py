"""As-of-safe orchestration for the Evidence Model v2 liquidity section."""

from __future__ import annotations

from datetime import datetime

from src.engine.liquidity import LiquidityEngine
from src.repositories.liquidity_repository import LiquidityRepository
from src.services.rule_registry import RuleRegistry


class MarketLiquidityService:
    def __init__(self, db_path: str = "data/cache.db", config: dict | None = None):
        self.repository = LiquidityRepository(db_path)
        settings = (config or {}).get("liquidity_v2", {})
        self.engine = LiquidityEngine(
            float(settings.get("elevated_percentile", 80.0)),
            float(settings.get("historically_high_percentile", 95.0)),
        )
        self.min_5y = int(settings.get("min_observations_5y", 1000))
        self.min_10y = int(settings.get("min_observations_10y", 2000))
        self.rules = RuleRegistry()

    def _trace(self, rule_id: str, cutoff: str) -> dict:
        rule = self.rules.describe(rule_id)
        return {
            "rule_id": rule_id,
            "rule_version": rule["version"],
            "evidence_level": rule["evidence_level"],
            "implementation_mode": rule["implementation_mode"],
            "project_operationalization": rule["project_operationalization"],
            "source_data_as_of": cutoff,
        }

    def analyze(self, knowledge_cutoff_at: str) -> dict:
        as_of_date = datetime.fromisoformat(
            knowledge_cutoff_at.replace("Z", "+00:00")
        ).date().isoformat()
        turnover_rows = self.repository.turnover_as_of(knowledge_cutoff_at)
        observations = []
        latest_partial = None
        for turnover in turnover_rows:
            if turnover["status"] != "available" or turnover["total_turnover_twd"] is None:
                latest_partial = turnover
                continue
            m1b = self.repository.m1b_for_turnover(turnover, knowledge_cutoff_at)
            if m1b is None:
                continue
            ratio = self.engine.ratio_pct(
                float(turnover["total_turnover_twd"]), float(m1b["value_twd"])
            )
            observations.append({
                "trade_date": turnover["trade_date"],
                "twse_turnover_twd": turnover["twse_turnover_twd"],
                "tpex_turnover_twd": turnover["tpex_turnover_twd"],
                "total_turnover_twd": turnover["total_turnover_twd"],
                "m1b_twd": m1b["value_twd"],
                "m1b_period": m1b["period"],
                "m1b_available_at": m1b["available_at"],
                "turnover_m1b_ratio_pct": ratio,
                "ratio_pct": ratio,
            })
        if observations:
            result = self.engine.evaluate(
                observations, as_of_date, self.min_5y, self.min_10y
            )
        elif latest_partial:
            result = self.engine.insufficient(
                as_of_date,
                "both_twse_and_tpex_turnover_are_required",
                status="partial",
                trade_date=latest_partial["trade_date"],
                twse_turnover_twd=latest_partial["twse_turnover_twd"],
                tpex_turnover_twd=latest_partial["tpex_turnover_twd"],
                total_turnover_twd=None,
            )
        else:
            m1b = self.repository.latest_m1b_as_of(knowledge_cutoff_at)
            reason = "market_turnover_missing" if m1b else "market_turnover_and_m1b_missing"
            result = self.engine.insufficient(as_of_date, reason)
        result.pop("ratio_pct", None)
        result["turnover_twd"] = {
            "twse": result.pop("twse_turnover_twd", None),
            "tpex": result.pop("tpex_turnover_twd", None),
            "total": result.pop("total_turnover_twd", None),
            "unit": "TWD",
        }
        result["m1b_twd"] = {
            "value": result.pop("m1b_twd", None),
            "period": result.pop("m1b_period", None),
            "available_at": result.pop("m1b_available_at", None),
            "unit": "TWD",
        }
        result["reference_case"] = {
            "range_pct": [3.3, 3.4],
            "label": "2026 公開案例參考帶",
            "meaning": "Publicly cited extreme-heat reference case; not a universal market-top or sell rule",
        }
        result["rules_used"] = [
            self._trace("LIQ-01", knowledge_cutoff_at),
            self._trace("LIQ-02", knowledge_cutoff_at),
        ]
        result["data_quality"] = {
            "status": result["status"],
            "complete_market_scope": result["turnover_twd"]["total"] is not None,
            "no_fixed_fallback": True,
        }
        return result
