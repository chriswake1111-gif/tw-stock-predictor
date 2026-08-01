import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.collectors.twse_official_collector import (
    TWSEOfficialCollector,
    parse_twse_exright,
    parse_twse_market_month,
    parse_twse_reduction,
    parse_twse_stock_day,
)
from tools.audit_twse_official_data import main as official_audit_main, reconcile


FIXTURES = Path("tests/fixtures")


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_official_twse_parsers_preserve_units_dates_and_event_semantics():
    market = parse_twse_market_month(load_fixture("twse_phase1_fmtqik.json"))[0]
    stock = parse_twse_stock_day(
        load_fixture("twse_phase1_stock_day.json"), symbol="1301.TW"
    )[0]
    exright = parse_twse_exright(load_fixture("twse_phase1_exright.json"))[0]
    reduction = parse_twse_reduction(load_fixture("twse_phase1_reduction.json"))[0]

    assert market["trade_date"] == "2014-01-02"
    assert market["traded_value_twd"] == 92115396580
    assert stock["trade_date"] == "2025-08-01"
    assert stock["volume"] == 50445289
    assert exright["event_type"] == "ex_dividend"
    assert exright["reference_price"] == 83.2
    assert reduction["event_date"] == "2018-10-26"
    assert reduction["event_type"] == "capital_reduction"
    assert reduction["reference_price"] == 82.62


def test_reduction_parser_accepts_single_dash_as_official_missing_value():
    payload = load_fixture("twse_phase1_reduction.json")
    payload["data"][0][3] = "-"

    reduction = parse_twse_reduction(payload)[0]

    assert reduction["previous_close"] is None


def test_schema_migration_is_parallel_and_does_not_modify_legacy_rows(tmp_path):
    db_path = tmp_path / "cache.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE daily_ohlcv (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume REAL, PRIMARY KEY(symbol, date)
            )
        """)
        conn.execute(
            "INSERT INTO daily_ohlcv VALUES ('2330.TW','2025-01-02',1,2,1,2,1000)"
        )

    TWSEOfficialCollector(str(db_path))

    with sqlite3.connect(db_path) as conn:
        legacy = conn.execute("SELECT * FROM daily_ohlcv").fetchall()
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert legacy == [("2330.TW", "2025-01-02", 1.0, 2.0, 1.0, 2.0, 1000.0)]
    assert {
        "twse_market_calendar", "twse_daily_raw", "twse_corporate_actions",
        "twse_data_audit_runs",
    }.issubset(tables)


def test_official_month_upserts_are_idempotent(tmp_path):
    collector = TWSEOfficialCollector(str(tmp_path / "cache.db"))
    payload = load_fixture("twse_phase1_stock_day.json")
    collector._fetch_json = lambda *_args, **_kwargs: (payload, "https://www.twse.com.tw/test", "abc")

    assert collector.fetch_stock_month("1301.TW", "2025-08") == 1
    assert collector.fetch_stock_month("1301.TW", "2025-08") == 1

    with sqlite3.connect(collector.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM twse_daily_raw").fetchone()[0]
    assert count == 1


def test_resume_rejects_reusing_audit_id_for_different_scope(tmp_path):
    collector = TWSEOfficialCollector(str(tmp_path / "cache.db"))
    collector.start_or_resume_audit(
        "audit-1", "snapshot-a", "2025-01-01", "2025-01-31",
        ["1301.TW"], {"mode": "execute"},
    )

    with pytest.raises(ValueError, match="different scope"):
        collector.start_or_resume_audit(
            "audit-1", "snapshot-b", "2025-01-01", "2025-01-31",
            ["1301.TW"], {"mode": "execute"},
        )


def test_dry_run_does_not_create_database(tmp_path, capsys):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    pd.DataFrame([{"symbol": "1301.TW"}]).to_csv(
        snapshot / "data_provenance.csv", index=False
    )
    pd.DataFrame([{
        "symbol": "1301.TW",
        "missing_reference_dates": "['2025-08-01']",
    }]).to_csv(snapshot / "data_quality.csv", index=False)
    db_path = tmp_path / "must-not-exist.db"

    exit_code = official_audit_main([
        "--snapshot-dir", str(snapshot),
        "--db-path", str(db_path),
        "--start", "2025-08-01",
        "--end", "2025-08-31",
        "--dry-run",
    ])

    assert exit_code == 0
    assert not db_path.exists()
    assert '"minimum_request_count": 4' in capsys.readouterr().out


def test_reconciliation_distinguishes_provider_gap_from_suspension(tmp_path):
    snapshot = tmp_path / "snapshot"
    data_dir = snapshot / "data"
    data_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "symbol": "1301.TW", "date": "2025-08-04", "open": 39.55,
            "high": 39.55, "low": 37.05, "close": 37.85, "volume": 64385687,
        }
    ]).to_csv(data_dir / "1301-TW.csv", index=False)
    collector = TWSEOfficialCollector(str(tmp_path / "cache.db"))
    with sqlite3.connect(collector.db_path) as conn:
        conn.execute("""
            INSERT INTO twse_daily_raw (
                symbol, trade_date, open, high, low, close, volume,
                traded_value_twd, transaction_count, change_text, note,
                source_dataset, source_url, fetched_at, payload_sha256
            ) VALUES ('1301.TW','2025-08-01',41.8,42.25,40.6,40.7,50445289,
                2076702924,27806,'-1.95','','STOCK_DAY','official','now','hash')
        """)

    result = reconcile(
        collector,
        snapshot,
        ["1301.TW"],
        {"2025-08-01", "2025-08-04"},
        {"1301.TW": ["2025-08-01", "2025-08-05"]},
        "2025-08-01",
        "2025-08-31",
    )

    summary = result["symbol_summaries"][0]
    assert summary["provider_missing_official_trade"] == 1
    assert summary["security_no_trade_or_suspended"] == 1
    assert summary["official_integrity_status"] == "quality_warning"
