import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.collectors.twse_official_collector import TWSEOfficialCollector
from src.research.backtest_evaluation import dataframe_sha256, evaluate_symbol_walk_forward
from src.research.official_gap_adjustment import (
    CONTRACT_VERSION,
    build_reconciled_snapshot,
    derive_adjusted_gap_row,
)
from tools.build_official_gap_snapshot import main as build_snapshot_main


def make_snapshot_frame() -> pd.DataFrame:
    factor = 0.9
    return pd.DataFrame([
        {
            "symbol": "1301.TW", "date": "2025-08-04", "open": 39.0 * factor,
            "high": 40.0 * factor, "low": 38.0 * factor,
            "close": 39.5 * factor, "volume": 1000,
        },
        {
            "symbol": "1301.TW", "date": "2025-08-05", "open": 40.0 * factor,
            "high": 41.0 * factor, "low": 39.0 * factor,
            "close": 40.5 * factor, "volume": 1100,
        },
    ])


def official_row(date: str, close: float, volume: int = 1000) -> dict:
    return {
        "symbol": "1301.TW", "trade_date": date, "open": close - 0.5,
        "high": close + 0.5, "low": close - 1.0, "close": close,
        "volume": volume, "source_dataset": "STOCK_DAY",
        "source_url": "https://www.twse.com.tw/official",
        "fetched_at": "2026-08-01T00:00:00+08:00", "payload_sha256": "hash",
    }


def write_source_and_audit(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    data_dir = source / "data"
    data_dir.mkdir(parents=True)
    frame = make_snapshot_frame()
    frame.to_csv(data_dir / "1301-TW.csv", index=False)
    pd.DataFrame([{
        "symbol": "1301.TW",
        "provider": "test adjusted parent",
        "auto_adjust": True,
        "zero_volume_rows_removed": 1,
        "zero_volume_dates_removed": "['2025-08-01']",
        "normalized_snapshot_sha256": dataframe_sha256(frame),
    }]).to_csv(source / "data_provenance.csv", index=False)
    audit = {
        "mode": "official_twse_integrity_audit",
        "symbol_summaries": [{
            "symbol": "1301.TW", "provider_row_on_non_market_day": 0,
        }],
        "classifications": [{
            "symbol": "1301.TW", "date": "2025-08-01",
            "classification": "provider_missing_official_trade",
        }],
        "request_plan": {"snapshot_dir": str(source.resolve())},
    }
    audit_path = source / "official_integrity_audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    db_path = tmp_path / "cache.db"
    TWSEOfficialCollector(str(db_path))
    rows = [
        official_row("2025-08-01", 40.0, 1200),
        official_row("2025-08-04", 39.5),
        official_row("2025-08-05", 40.5),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO twse_market_calendar "
            "VALUES ('2025-08-01',1,1,1,1,1,'FMTQIK','official','now','hash')"
        )
        conn.executemany("""
            INSERT INTO twse_daily_raw (
                symbol, trade_date, open, high, low, close, volume,
                traded_value_twd, transaction_count, change_text, note,
                source_dataset, source_url, fetched_at, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, '', '', ?, ?, ?, ?)
        """, [
            (
                row["symbol"], row["trade_date"], row["open"], row["high"],
                row["low"], row["close"], row["volume"], row["source_dataset"],
                row["source_url"], row["fetched_at"], row["payload_sha256"],
            )
            for row in rows
        ])
    return source, audit_path, db_path


def test_gap_derivation_requires_unique_factor_consensus():
    frame = make_snapshot_frame()
    rows = {
        "2025-08-01": official_row("2025-08-01", 40.0, 1200),
        "2025-08-04": official_row("2025-08-04", 39.5),
        "2025-08-05": official_row("2025-08-05", 40.5),
    }

    result = derive_adjusted_gap_row(
        "1301.TW", "2025-08-01", frame, rows, []
    )

    assert result["status"] == "available"
    assert result["contract_version"] == CONTRACT_VERSION
    assert result["adjustment_factor"] == pytest.approx(0.9)
    assert result["adjusted_row"]["close"] == pytest.approx(36.0)
    assert result["adjusted_row"]["volume"] == 1200.0


def test_gap_derivation_fails_closed_for_ambiguous_consensus():
    frame = pd.DataFrame([
        {"symbol": "1301.TW", "date": "2025-08-04", "close": 90, "open": 90,
         "high": 90, "low": 90, "volume": 1},
        {"symbol": "1301.TW", "date": "2025-08-05", "close": 90, "open": 90,
         "high": 90, "low": 90, "volume": 1},
        {"symbol": "1301.TW", "date": "2025-08-06", "close": 80, "open": 80,
         "high": 80, "low": 80, "volume": 1},
        {"symbol": "1301.TW", "date": "2025-08-07", "close": 80, "open": 80,
         "high": 80, "low": 80, "volume": 1},
    ])
    rows = {"2025-08-01": official_row("2025-08-01", 100)}
    for date in frame["date"]:
        rows[date] = official_row(date, 100)

    result = derive_adjusted_gap_row(
        "1301.TW", "2025-08-01", frame, rows, []
    )

    assert result["status"] == "insufficient_data"
    assert result["reason"] == "ambiguous_factor_consensus"


def test_gap_derivation_never_uses_anchors_across_action_boundary():
    frame = pd.DataFrame([
        {"symbol": "1301.TW", "date": "2025-08-04", "close": 90, "open": 90,
         "high": 90, "low": 90, "volume": 1},
        {"symbol": "1301.TW", "date": "2025-08-05", "close": 80, "open": 80,
         "high": 80, "low": 80, "volume": 1},
        {"symbol": "1301.TW", "date": "2025-08-06", "close": 80, "open": 80,
         "high": 80, "low": 80, "volume": 1},
    ])
    rows = {"2025-08-01": official_row("2025-08-01", 100)}
    for date in frame["date"]:
        rows[date] = official_row(date, 100)

    result = derive_adjusted_gap_row(
        "1301.TW", "2025-08-01", frame, rows, ["2025-08-05"]
    )

    assert result["status"] == "insufficient_data"
    assert result["reason"] == "fewer_than_two_same_segment_anchors"
    assert result["candidate_anchor_count"] == 1


def test_builder_is_reversible_and_reconciles_official_zero_artifact(tmp_path):
    source, audit_path, db_path = write_source_and_audit(tmp_path)
    source_hash_before = hashlib.sha256(
        (source / "data" / "1301-TW.csv").read_bytes()
    ).hexdigest()

    result = build_reconciled_snapshot(source, audit_path, str(db_path))

    assert result.summary["resolved_gap_count"] == 1
    assert result.summary["unresolved_gap_count"] == 0
    assert result.summary["quality_approved_symbol_count"] == 1
    assert "2025-08-01" in set(result.snapshots["1301.TW"]["date"])
    assert hashlib.sha256(
        (source / "data" / "1301-TW.csv").read_bytes()
    ).hexdigest() == source_hash_before
    provenance = result.provenance[0]
    assert provenance["parent_normalized_snapshot_sha256"] == dataframe_sha256(
        make_snapshot_frame()
    )
    assert provenance["official_integrity_status"] == "available"
    assert provenance["zero_volume_reconciliation_status"] == "available"


def test_dry_run_does_not_create_output(tmp_path, capsys):
    source, audit_path, db_path = write_source_and_audit(tmp_path)
    output_root = tmp_path / "output"

    exit_code = build_snapshot_main([
        "--source-snapshot", str(source),
        "--audit-report", str(audit_path),
        "--db-path", str(db_path),
        "--output-root", str(output_root),
        "--run-id", "test-run",
        "--dry-run",
        "--json",
    ])

    assert exit_code == 0
    assert not output_root.exists()
    summary = json.loads(capsys.readouterr().out)
    assert summary["resolved_gap_count"] == 1
    assert summary["dry_run"] is True


def test_evaluation_honors_official_integrity_and_adjustment_contract():
    frame = pd.DataFrame([
        {
            "symbol": "1301.TW", "date": date.strftime("%Y-%m-%d"),
            "open": 100 + index * 0.1, "high": 101 + index * 0.1,
            "low": 99 + index * 0.1, "close": 100 + index * 0.1,
            "volume": 1000,
        }
        for index, date in enumerate(pd.bdate_range("2020-01-01", periods=300))
    ])
    evaluation = evaluate_symbol_walk_forward(
        "1301.TW", frame, requested_start=frame.iloc[150]["date"],
        requested_end=frame.iloc[-1]["date"], validation_months=3,
        warmup_bars=50, min_validation_bars=40, sensitivity_enabled=False,
        data_provenance={
            "adjustment_contract": CONTRACT_VERSION,
            "official_integrity_status": "quality_warning",
            "official_gap_rows_inserted": 10,
            "official_gap_rows_unresolved": 1,
            "zero_volume_rows_removed": 2,
            "zero_volume_reconciliation_status": "available",
        },
    )

    quality = evaluation["data_quality"]
    assert quality["status"] == "quality_warning"
    assert quality["official_gap_rows_unresolved"] == 1
    assert quality["price_adjustment_contract"] == CONTRACT_VERSION


def test_evaluation_exempts_only_officially_explained_reference_dates():
    frame = pd.DataFrame([
        {
            "symbol": "1301.TW", "date": date.strftime("%Y-%m-%d"),
            "open": 100, "high": 101, "low": 99, "close": 100,
            "volume": 1000,
        }
        for date in pd.bdate_range("2020-01-01", periods=300)
    ])
    missing_date = str(frame.iloc[100]["date"])
    target = frame.drop(index=100).reset_index(drop=True)

    evaluation = evaluate_symbol_walk_forward(
        "1301.TW", target, requested_start="2099-01-01",
        requested_end="2099-12-31", benchmark_df=frame,
        sensitivity_enabled=False, data_provenance={
            "adjustment_contract": CONTRACT_VERSION,
            "official_integrity_status": "available",
            "official_gap_rows_inserted": 1,
            "official_gap_rows_unresolved": 0,
            "official_reference_exempt_dates": json.dumps([missing_date]),
            "official_explained_no_bar_dates": json.dumps([missing_date]),
            "benchmark_provider_non_market_dates": "[]",
            "zero_volume_reconciliation_status": "available",
        },
    )

    quality = evaluation["data_quality"]
    assert quality["status"] == "available"
    assert quality["missing_reference_date_count"] == 0
    assert quality["official_reference_exempt_dates_applied"] == [missing_date]


def test_evaluation_rejects_unsubstantiated_reference_exemption():
    frame = pd.DataFrame([
        {
            "symbol": "1301.TW", "date": date.strftime("%Y-%m-%d"),
            "open": 100, "high": 101, "low": 99, "close": 100,
            "volume": 1000,
        }
        for date in pd.bdate_range("2020-01-01", periods=300)
    ])
    missing_date = str(frame.iloc[100]["date"])
    target = frame.drop(index=100).reset_index(drop=True)

    evaluation = evaluate_symbol_walk_forward(
        "1301.TW", target, requested_start="2099-01-01",
        requested_end="2099-12-31", benchmark_df=frame,
        sensitivity_enabled=False, data_provenance={
            "adjustment_contract": CONTRACT_VERSION,
            "official_integrity_status": "available",
            "official_gap_rows_inserted": 1,
            "official_gap_rows_unresolved": 0,
            "official_reference_exempt_dates": json.dumps([missing_date]),
            "official_explained_no_bar_dates": "[]",
            "benchmark_provider_non_market_dates": "[]",
            "zero_volume_reconciliation_status": "available",
        },
    )

    quality = evaluation["data_quality"]
    assert quality["status"] == "quality_warning"
    assert quality["missing_reference_dates"] == [missing_date]
    assert quality["official_reference_exemption_contract_valid"] is False
