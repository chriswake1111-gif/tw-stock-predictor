"""Deterministic Evidence Model v2 turnover-to-M1B calculations."""

from __future__ import annotations

from datetime import date
from typing import Any


class LiquidityEngine:
    def __init__(self, elevated_percentile: float = 80.0, high_percentile: float = 95.0):
        if not 0 < elevated_percentile < high_percentile <= 100:
            raise ValueError("liquidity percentile thresholds are invalid")
        self.elevated_percentile = elevated_percentile
        self.high_percentile = high_percentile

    @staticmethod
    def ratio_pct(turnover_twd: float, m1b_twd: float) -> float:
        if turnover_twd <= 0 or m1b_twd <= 0:
            raise ValueError("turnover and M1B must be greater than zero")
        return round(turnover_twd / m1b_twd * 100.0, 8)

    @staticmethod
    def rolling_mean(values: list[float], window: int) -> tuple[float | None, int]:
        sample = values[-window:]
        if len(sample) < window:
            return None, len(sample)
        return round(sum(sample) / window, 8), len(sample)

    @staticmethod
    def percentile(current: float, values: list[float]) -> float | None:
        """Return deterministic upper-rank empirical percentile; ties count as <=."""
        if not values:
            return None
        return round(sum(value <= current for value in values) / len(values) * 100.0, 4)

    @staticmethod
    def _years_ago(value: date, years: int) -> date:
        try:
            return value.replace(year=value.year - years)
        except ValueError:
            return value.replace(year=value.year - years, day=28)

    def evaluate(
        self,
        observations: list[dict[str, Any]],
        as_of_date: str,
        min_observations_5y: int = 1000,
        min_observations_10y: int = 2000,
    ) -> dict[str, Any]:
        cutoff_date = date.fromisoformat(as_of_date)
        valid = sorted(
            (
                row for row in observations
                if row.get("ratio_pct") is not None
                and date.fromisoformat(row["trade_date"]) <= cutoff_date
            ),
            key=lambda row: row["trade_date"],
        )
        if not valid:
            return self.insufficient(as_of_date, "complete_turnover_and_m1b_missing")
        current = valid[-1]
        current_date = date.fromisoformat(current["trade_date"])
        ratios = [float(row["ratio_pct"]) for row in valid]
        mean20, count20 = self.rolling_mean(ratios, 20)
        mean60, count60 = self.rolling_mean(ratios, 60)

        def window_result(years: int, minimum: int) -> tuple[float | None, int]:
            start = self._years_ago(current_date, years)
            sample = [
                float(row["ratio_pct"]) for row in valid
                if start <= date.fromisoformat(row["trade_date"]) <= current_date
            ]
            covers_window = date.fromisoformat(valid[0]["trade_date"]) <= start
            if not covers_window or len(sample) < minimum:
                return None, len(sample)
            return self.percentile(float(current["ratio_pct"]), sample), len(sample)

        percentile5, count5 = window_result(5, min_observations_5y)
        percentile10, count10 = window_result(10, min_observations_10y)
        heat_percentile = percentile5 if percentile5 is not None else percentile10
        ratio = float(current["ratio_pct"])
        if 3.3 <= ratio <= 3.4:
            alert = "reference_extreme_case"
        elif heat_percentile is not None and heat_percentile >= self.high_percentile:
            alert = "historically_high"
        elif heat_percentile is not None and heat_percentile >= self.elevated_percentile:
            alert = "elevated"
        else:
            alert = "normal"
        return {
            "status": "available",
            "as_of_date": as_of_date,
            **current,
            "rolling_mean_20d_pct": mean20,
            "rolling_mean_20d_observation_count": count20,
            "rolling_mean_60d_pct": mean60,
            "rolling_mean_60d_observation_count": count60,
            "historical_percentile_5y": percentile5,
            "historical_percentile_10y": percentile10,
            "historical_observation_count_5y": count5,
            "historical_observation_count_10y": count10,
            "alert_level": alert,
        }

    @staticmethod
    def insufficient(as_of_date: str, reason: str, **details: Any) -> dict[str, Any]:
        return {
            "status": details.pop("status", "insufficient_data"),
            "as_of_date": as_of_date,
            "reason": reason,
            "turnover_m1b_ratio_pct": None,
            "rolling_mean_20d_pct": None,
            "rolling_mean_60d_pct": None,
            "historical_percentile_5y": None,
            "historical_percentile_10y": None,
            "alert_level": "insufficient_data",
            **details,
        }
