"""As-of-safe orchestration for the Evidence Model v2 liquidity section."""

from __future__ import annotations

from datetime import datetime
import sqlite3

from src.engine.liquidity import LiquidityEngine
from src.repositories.liquidity_repository import LiquidityRepository
from src.services.rule_registry import RuleRegistry


class MarketLiquidityService:
    def __init__(
        self,
        db_path: str = "data/cache.db",
        config: dict | None = None,
        *,
        auto_migrate: bool = True,
    ):
        self.repository = LiquidityRepository(db_path, auto_migrate=auto_migrate)
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
        with self.repository._connect() as conn:
            return self._analyze_with_connection(conn, knowledge_cutoff_at)

    def analyze_preloaded(
        self, connection: sqlite3.Connection, knowledge_cutoff_at: str
    ) -> dict:
        """Evaluate liquidity evidence on a caller-owned read transaction."""
        return self._analyze_with_connection(connection, knowledge_cutoff_at)

    def _analyze_with_connection(
        self, connection: sqlite3.Connection, knowledge_cutoff_at: str
    ) -> dict:
        as_of_date = datetime.fromisoformat(
            knowledge_cutoff_at.replace("Z", "+00:00")
        ).date().isoformat()
        turnover_rows = self.repository.turnover_as_of_with_connection(
            connection, knowledge_cutoff_at
        )
        observations = []
        source_resource_versions = []
        for turnover in turnover_rows:
            if turnover["status"] != "available" or turnover["total_turnover_twd"] is None:
                continue
            m1b = self.repository.m1b_for_turnover_with_connection(
                connection, turnover, knowledge_cutoff_at
            )
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
            source_resource_versions.extend([
                {
                    "section": "liquidity",
                    "resource_type": "market_turnover_revision",
                    "resource_id": turnover["id"],
                    "logical_resource_id": turnover["trade_date"],
                    "revision_number": turnover["revision"],
                    "available_at": turnover["available_at"],
                    "ingested_at": turnover["ingested_at"],
                    "approval_ids": [],
                },
                {
                    "section": "liquidity",
                    "resource_type": "m1b_revision",
                    "resource_id": m1b["id"],
                    "logical_resource_id": m1b["period"],
                    "revision_number": m1b["revision"],
                    "available_at": m1b["available_at"],
                    "ingested_at": m1b["ingested_at"],
                    "approval_ids": [],
                },
            ])
        latest_turnover = turnover_rows[-1] if turnover_rows else None
        latest_observation = observations[-1] if observations else None
        latest_is_analyzable = (
            latest_turnover is not None
            and latest_turnover["status"] == "available"
            and latest_observation is not None
            and latest_observation["trade_date"] == latest_turnover["trade_date"]
        )
        if latest_is_analyzable:
            result = self.engine.evaluate(
                observations, as_of_date, self.min_5y, self.min_10y
            )
        elif latest_turnover and latest_turnover["status"] == "partial":
            result = self.engine.insufficient(
                as_of_date,
                "both_twse_and_tpex_turnover_are_required",
                status="partial",
                trade_date=latest_turnover["trade_date"],
                twse_turnover_twd=latest_turnover["twse_turnover_twd"],
                tpex_turnover_twd=latest_turnover["tpex_turnover_twd"],
                total_turnover_twd=None,
            )
        elif latest_turnover and latest_turnover["status"] == "revoked":
            result = self.engine.insufficient(
                as_of_date,
                "latest_market_turnover_revoked",
                trade_date=latest_turnover["trade_date"],
            )
        elif latest_turnover:
            result = self.engine.insufficient(
                as_of_date,
                "m1b_unavailable_for_latest_turnover",
                trade_date=latest_turnover["trade_date"],
            )
        else:
            m1b = self.repository.latest_m1b_as_of_with_connection(
                connection, knowledge_cutoff_at
            )
            reason = "market_turnover_missing" if m1b else "market_turnover_and_m1b_missing"
            result = self.engine.insufficient(as_of_date, reason)
        if not latest_is_analyzable and latest_observation:
            result["latest_complete_observation"] = {
                key: value for key, value in latest_observation.items()
                if key != "ratio_pct"
            }
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
        result["source_resource_versions"] = source_resource_versions
        return result
