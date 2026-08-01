from pathlib import Path

from src.research.backtest_evaluation import assess_expanded_universe
import pandas as pd
import pytest

from tools.run_backtest import (
    apply_snapshot_overrides,
    load_snapshot_overrides,
    load_universe_config,
)


CONFIG_PATH = Path("config/expanded_validation_universe.yaml")


def make_evaluation(symbol: str, *, available: bool = True, passed: bool = True, mdd: float = 8.0):
    return {
        "symbol": symbol,
        "status": "available" if available else "insufficient_data",
        "adaptive_research_gate": {"passed": passed},
        "data_quality": {"status": "available"},
        "aggregates": {
            "adaptive_tu_strategy": {"worst_window_drawdown_pct": mdd}
        },
    }


def test_expanded_universe_is_preregistered_and_disjoint_from_reference_symbols():
    config = load_universe_config(str(CONFIG_PATH))
    symbols = [item["symbol"] for item in config["symbols"]]

    assert config["status"] == "preregistered_before_backtest"
    assert len(symbols) == 14
    assert len(symbols) == len(set(symbols))
    assert {"^TWII", "0050.TW", "2330.TW"}.isdisjoint(symbols)
    assert config["promotion_policy"]["default_promotion"] is False


def test_universe_gate_counts_unavailable_and_failed_symbols_without_dropping_them():
    config = load_universe_config(str(CONFIG_PATH))
    symbols = [item["symbol"] for item in config["symbols"]]
    evaluations = [make_evaluation(symbol) for symbol in symbols]
    evaluations[0] = make_evaluation(symbols[0], available=False)
    evaluations[1] = make_evaluation(symbols[1], passed=False)

    assessment = assess_expanded_universe(evaluations, config)

    assert assessment["total_symbols"] == 14
    assert assessment["usable_symbols"] == 13
    assert assessment["passed_symbols"] == 12
    assert assessment["gate_pass_rate"] == 12 / 14
    assert assessment["passed"] is True
    assert assessment["promotion_to_default"] is False


def test_universe_gate_holds_when_any_preregistered_threshold_fails():
    config = load_universe_config(str(CONFIG_PATH))
    symbols = [item["symbol"] for item in config["symbols"]]
    evaluations = [make_evaluation(symbol) for symbol in symbols]
    evaluations[-1] = make_evaluation(symbols[-1], mdd=12.01)

    assessment = assess_expanded_universe(evaluations, config)

    assert assessment["checks"]["maximum_symbol_mdd_met"] is False
    assert assessment["status"] == "hold_for_revision"
    assert assessment["passed"] is False


def test_snapshot_override_only_inserts_declared_missing_adjusted_row():
    config = load_snapshot_overrides("config/research_snapshot_overrides.yaml")
    frame = pd.DataFrame([
        {
            "symbol": "1301.TW",
            "date": "2025-07-31",
            "open": 42.0,
            "high": 43.2,
            "low": 41.9,
            "close": 42.65 * 0.9906014803565542,
            "volume": 1000,
        },
        {
            "symbol": "1301.TW",
            "date": "2025-08-04",
            "open": 39.0,
            "high": 39.2,
            "low": 37.0,
            "close": 37.85 * 0.9906014803565542,
            "volume": 1000,
        },
    ])
    snapshots = {"1301.TW": frame}
    provenance = {"1301.TW": {"provider": "test"}}

    apply_snapshot_overrides(snapshots, provenance, config)

    inserted = snapshots["1301.TW"].loc[
        snapshots["1301.TW"]["date"] == "2025-08-01"
    ].iloc[0]
    assert round(inserted["close"], 6) == round(40.70 * 0.9906014803565542, 6)
    assert provenance["1301.TW"]["snapshot_overrides"][0]["source_url"].startswith(
        "https://www.twse.com.tw/"
    )
    with pytest.raises(ValueError, match="refuses to replace existing row"):
        apply_snapshot_overrides(snapshots, provenance, config)
