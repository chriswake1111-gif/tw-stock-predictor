import json
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.research.backtest_evaluation import (
    CostModel,
    build_walk_forward_windows,
    classify_market_regime,
    cost_model_for_symbol,
    dataframe_sha256,
    evaluate_symbol_walk_forward,
    evaluate_training_parameter_sensitivity,
    load_cached_ohlcv,
    simulate_buy_and_hold,
    simulate_sma_baseline,
    select_adaptive_allocation_profile,
    write_walk_forward_report,
)
from tools.run_backtest import main as run_backtest_main


def make_market_df(days=800):
    dates = [
        (datetime(2021, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(days)
    ]
    trend = np.concatenate([
        np.linspace(100, 180, days // 2),
        np.linspace(180, 130, days - days // 2),
    ])
    cycle = np.sin(np.arange(days) / 12.0) * 5.0
    close = trend + cycle
    return pd.DataFrame({
        "symbol": ["2330.TW"] * days,
        "date": dates,
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 10000 + np.arange(days) * 10,
    })


def test_cost_model_distinguishes_stock_etf_and_index_proxy():
    assert cost_model_for_symbol("2330.TW").sales_tax_rate == 0.003
    assert cost_model_for_symbol("0050.TW").sales_tax_rate == 0.001
    assert cost_model_for_symbol("^TWII").instrument_type == "index_etf_proxy"
    assert cost_model_for_symbol("^TWII").lot_size == 1


def test_cached_loader_and_dataset_hash_are_reproducible(tmp_path):
    db_path = str(tmp_path / "cache.db")
    frame = make_market_df(days=10)
    with sqlite3.connect(db_path) as conn:
        frame.to_sql("daily_ohlcv", conn, index=False)

    first = load_cached_ohlcv(db_path, "2330", end_date="2021-01-10")
    second = load_cached_ohlcv(db_path, "2330.TW", end_date="2021-01-10")

    assert len(first) == 10
    assert dataframe_sha256(first) == dataframe_sha256(second)


def test_walk_forward_windows_require_warmup_and_minimum_validation():
    frame = make_market_df()

    windows = build_walk_forward_windows(
        frame,
        requested_start="2021-01-01",
        requested_end=frame.iloc[-1]["date"],
        validation_months=6,
        warmup_bars=233,
        min_validation_bars=120,
    )

    assert windows
    assert all(window["warmup_bars"] == 233 for window in windows)
    assert all(window["validation_bars"] >= 120 for window in windows)


def test_market_regime_is_post_hoc_and_uses_benchmark_return():
    frame = make_market_df(days=300)
    regime = classify_market_regime(
        frame,
        frame.iloc[0]["date"],
        frame.iloc[149]["date"],
        threshold=0.15,
    )

    assert regime["label"] == "bull"
    assert regime["classification_usage"] == "post_hoc_reporting_only"


def test_explicit_adjusted_source_overrides_legacy_quality_label():
    frame = make_market_df()
    evaluation = evaluate_symbol_walk_forward(
        "2330.TW",
        frame,
        requested_start="2021-01-01",
        requested_end=frame.iloc[-1]["date"],
        validation_months=6,
        warmup_bars=233,
        min_validation_bars=120,
        data_provenance={"provider": "test", "auto_adjust": True},
        sensitivity_enabled=False,
    )

    assert evaluation["data_quality"]["price_adjustment_contract"] == (
        "yfinance auto_adjust=True; actions=False; repair=False"
    )


def test_parameter_sensitivity_uses_training_data_before_validation_only():
    frame = make_market_df(days=1300)
    windows = build_walk_forward_windows(
        frame,
        requested_start=frame.iloc[600]["date"],
        requested_end=frame.iloc[-1]["date"],
        validation_months=6,
        warmup_bars=233,
        min_validation_bars=120,
    )
    sensitivity = evaluate_training_parameter_sensitivity(
        frame,
        windows,
        initial_cash=1_000_000.0,
        costs=CostModel(),
        candidates=[{
            "candidate_id": "default",
            "strategy_params": {
                "stage1_ratio": 0.20,
                "pullback_min_pct": 0.07,
                "pullback_max_pct": 0.11,
            },
        }],
        lookback_bars=300,
        min_training_bars=200,
    )

    assert sensitivity["status"] == "available"
    assert sensitivity["selection_applied_to_validation"] is False
    assert all(
        window["training_end"] < window["validation_start"]
        for window in sensitivity["training_windows"]
    )


def test_adaptive_profile_selection_is_training_only_and_has_guardrails():
    frame = make_market_df(days=1300)
    selection = select_adaptive_allocation_profile(
        frame,
        validation_start_index=900,
        initial_cash=1_000_000.0,
        costs=CostModel(),
    )

    assert selection["training_end"] < selection["validation_start"]
    assert selection["selected_profile_id"] in {
        "legacy",
        "balanced",
    }
    assert selection["max_training_drawdown_pct"] == 12.0
    assert len(selection["candidate_results"]) == 2


def test_adaptive_profile_selection_falls_back_without_training_history():
    frame = make_market_df(days=500)
    selection = select_adaptive_allocation_profile(
        frame,
        validation_start_index=200,
        initial_cash=1_000_000.0,
        costs=CostModel(),
    )

    assert selection["status"] == "fallback"
    assert selection["selected_profile_id"] == "legacy"
    assert selection["reason"] == "insufficient_pre_validation_training_data"


def test_baselines_include_costs_and_next_bar_signal_timing():
    frame = make_market_df(days=300)
    costs = CostModel()

    buy_hold = simulate_buy_and_hold(frame.iloc[-120:], 1_000_000.0, costs)
    sma = simulate_sma_baseline(
        frame,
        validation_start=frame.iloc[-120]["date"],
        validation_end=frame.iloc[-1]["date"],
        initial_cash=1_000_000.0,
        costs=costs,
    )

    assert buy_hold["commission_and_tax_paid"] > 0
    assert buy_hold["average_capital_utilization_pct"] > 0
    assert buy_hold["mode"] == "historical_backtest_only"
    assert sma["execution_capability"] == "simulated_orders_only"
    for trade in sma["trades"]:
        if trade["action"] != "sell_terminal_liquidation":
            assert trade["signal_date"] < trade["execution_date"]


def test_walk_forward_evaluation_and_report_artifacts(tmp_path):
    frame = make_market_df()
    evaluation = evaluate_symbol_walk_forward(
        "2330.TW",
        frame,
        initial_cash=1_000_000.0,
        requested_start="2021-01-01",
        requested_end=frame.iloc[-1]["date"],
        validation_months=6,
        warmup_bars=233,
        min_validation_bars=120,
    )

    assert evaluation["status"] == "available"
    assert evaluation["optimization_performed"] is False
    assert evaluation["aggregates"]["tu_strategy"]["status"] == "available"
    assert evaluation["aggregates"]["adaptive_tu_strategy"]["status"] == "available"
    assert evaluation["validation_data_used_for_profile_selection"] is False
    assert evaluation["adaptive_research_gate"]["promotion_to_default"] is False
    assert "excess_vs_buy_hold_pct" in evaluation["aggregates"]["tu_strategy"]
    assert all(
        selection["training_end"] < selection["validation_start"]
        for selection in (
            window["adaptive_profile_selection"]
            for window in evaluation["windows"]
        )
        if selection["training_end"] is not None
    )

    run_dir = write_walk_forward_report(
        [evaluation],
        {"2330.TW": frame},
        output_dir=str(tmp_path / "reports"),
        run_timestamp="test-run",
    )

    assert (run_dir / "report.json").exists()
    assert (run_dir / "aggregate.csv").exists()
    assert (run_dir / "windows.csv").exists()
    assert (run_dir / "trades.csv").exists()
    assert (run_dir / "data_quality.csv").exists()
    assert (run_dir / "data_provenance.csv").exists()
    assert (run_dir / "regime_aggregate.csv").exists()
    assert (run_dir / "parameter_sensitivity.csv").exists()
    assert (run_dir / "adaptive_profile_selection.csv").exists()
    assert (run_dir / "adaptive_research_gate.csv").exists()
    assert (run_dir / "SUMMARY.md").exists()
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == 2
    assert report["execution_capability"] == "simulated_orders_only"
    windows_csv = (run_dir / "windows.csv").read_text(encoding="utf-8")
    assert "execution_log" not in windows_csv.splitlines()[0]

    replay_exit = run_backtest_main([
        "--symbol",
        "2330",
        "--snapshot-dir",
        str(run_dir),
        "--start",
        "2021-01-01",
        "--end",
        frame.iloc[-1]["date"],
        "--validation-months",
        "6",
        "--warmup-bars",
        "233",
        "--min-validation-bars",
        "120",
        "--output-dir",
        str(tmp_path / "replay"),
        "--run-id",
        "snapshot-replay",
        "--no-sensitivity",
        "--json",
    ])
    assert replay_exit == 0


def test_partial_model_does_not_publish_incomparable_excess_returns():
    frame = make_market_df()
    evaluation = evaluate_symbol_walk_forward(
        "2330.TW",
        frame,
        initial_cash=100.0,
        requested_start="2021-01-01",
        requested_end=frame.iloc[-1]["date"],
        validation_months=6,
        warmup_bars=233,
        min_validation_bars=120,
    )

    assert evaluation["aggregates"]["buy_and_hold"]["status"] in {
        "partial",
        "insufficient_data",
    }
    assert "excess_vs_buy_hold_pct" not in evaluation["aggregates"]["tu_strategy"]


def test_cli_runs_offline_and_writes_reproducible_artifacts(tmp_path):
    db_path = tmp_path / "cache.db"
    frame = make_market_df()
    with sqlite3.connect(db_path) as conn:
        frame.to_sql("daily_ohlcv", conn, index=False)

    exit_code = run_backtest_main([
        "--symbol",
        "2330",
        "--start",
        "2021-01-01",
        "--end",
        frame.iloc[-1]["date"],
        "--cash",
        "10000000",
        "--validation-months",
        "6",
        "--warmup-bars",
        "233",
        "--min-validation-bars",
        "120",
        "--db-path",
        str(db_path),
        "--output-dir",
        str(tmp_path / "reports"),
        "--run-id",
        "cli-test",
        "--no-sensitivity",
        "--json",
    ])

    assert exit_code == 0
    run_dirs = list((tmp_path / "reports").iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "report.json").exists()
    assert (run_dirs[0] / "data" / "2330-TW.csv").exists()
