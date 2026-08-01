import os
import sqlite3
import pytest
import pandas as pd
from datetime import datetime, timedelta

from src.collectors.twse_collector import TWSECollector
from src.collectors.finmind_collector import FinMindCollector

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_cache.db"
    return str(db_file)

def test_finmind_normalize_quarterly_eps():
    collector = FinMindCollector()
    raw_records = [
        {"date": "2025-03-31", "type": "EPS", "value": 2.0, "origin_date": "2025-05-10"},
        {"date": "2025-06-30", "type": "EPS", "value": 5.0, "origin_date": "2025-08-10"}, # Q2 累計 5.0 -> 單季 3.0
        {"date": "2025-09-30", "type": "EPS", "value": 8.0, "origin_date": "2025-11-10"}, # Q3 累計 8.0 -> 單季 3.0
        {"date": "2025-12-31", "type": "EPS", "value": 12.0, "origin_date": "2026-03-15"} # 全年 12.0 -> Q4 單季 4.0
    ]
    raw_df = pd.DataFrame(raw_records)
    norm_df = collector.normalize_quarterly_eps(raw_df)
    
    assert len(norm_df) == 4
    eps_list = norm_df["single_quarter_eps"].tolist()
    assert eps_list == [2.0, 3.0, 3.0, 4.0]
    assert sum(eps_list) == 12.0

def test_finmind_normalize_quarterly_eps_with_negative_quarter():
    collector = FinMindCollector()
    raw_records = [
        {"date": "2025-03-31", "type": "EPS", "value": 3.0, "origin_date": "2025-05-10"},
        {"date": "2025-06-30", "type": "EPS", "value": 5.0, "origin_date": "2025-08-10"}, # Q2 單季 2.0
        {"date": "2025-09-30", "type": "EPS", "value": 8.0, "origin_date": "2025-11-10"}, # Q3 單季 3.0
        {"date": "2025-12-31", "type": "EPS", "value": 6.0, "origin_date": "2026-03-15"} # 全年 6.0 -> Q4 單季 -2.0 (虧損)
    ]
    raw_df = pd.DataFrame(raw_records)
    norm_df = collector.normalize_quarterly_eps(raw_df)
    
    assert len(norm_df) == 4
    eps_list = norm_df["single_quarter_eps"].tolist()
    assert eps_list == [3.0, 2.0, 3.0, -2.0]
    assert sum(eps_list) == 6.0

def test_ttm_eps_rejects_non_consecutive_quarters():
    collector = FinMindCollector.__new__(FinMindCollector)
    gapped = pd.DataFrame([
        {"period_end": "2024-03-31", "available_at": "2024-05-01", "single_quarter_eps": 1.0, "year": 2024, "quarter": 1},
        {"period_end": "2024-09-30", "available_at": "2024-11-01", "single_quarter_eps": 2.0, "year": 2024, "quarter": 3},
        {"period_end": "2024-12-31", "available_at": "2025-03-01", "single_quarter_eps": 3.0, "year": 2024, "quarter": 4},
        {"period_end": "2025-03-31", "available_at": "2025-05-01", "single_quarter_eps": 4.0, "year": 2025, "quarter": 1},
    ])
    collector.get_quarterly_eps = lambda stock_id, as_of_date=None: gapped

    result = collector.get_ttm_eps("2330", as_of_date="2025-06-01")

    assert result["status"] == "insufficient_data"
    assert result["eps"] is None

def test_finmind_yield_rate_normalization():
    assert FinMindCollector._yield_to_ratio(3.5, "percent") == pytest.approx(0.035)
    assert FinMindCollector._yield_to_ratio(0.5, "percent") == pytest.approx(0.005)
    assert FinMindCollector._yield_to_ratio(0.035, "ratio") == pytest.approx(0.035)

def test_finmind_migrates_legacy_valuation_symbol_column(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE stock_valuation (
                symbol TEXT,
                date TEXT,
                pe REAL,
                pb REAL,
                yield_rate REAL,
                PRIMARY KEY (symbol, date)
            )
        """)
        conn.execute(
            "INSERT INTO stock_valuation VALUES (?, ?, ?, ?, ?)",
            ("2330", "2025-01-02", 20.0, 5.0, 2.5)
        )

    FinMindCollector(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(stock_valuation)")]
        row = conn.execute(
            "SELECT stock_id, yield_unit FROM stock_valuation"
        ).fetchone()

    assert "stock_id" in columns
    assert "symbol" not in columns
    assert row == ("2330", "percent")

def test_twse_collector_cache_init(temp_db):
    collector = TWSECollector(db_path=temp_db)
    assert os.path.exists(temp_db)
    
    # Verify table schema
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    assert "daily_ohlcv" in tables


def test_twse_research_snapshot_records_provenance_and_persists_cache(temp_db, monkeypatch):
    collector = TWSECollector(db_path=temp_db)
    stale = pd.DataFrame({
        "date": ["2014-01-01"],
        "open": [90.0],
        "high": [91.0],
        "low": [89.0],
        "close": [90.0],
        "volume": [900.0],
    })
    collector._save_cache("2330.TW", stale)
    frame = pd.DataFrame({
        "date": ["2014-01-02", "2014-01-03"],
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000.0, 1100.0],
    })
    calls = []

    def fake_fetch(symbol, start_date, exclusive_end):
        calls.append((symbol, start_date, exclusive_end))
        return frame.copy()

    monkeypatch.setattr(collector, "_fetch_from_yfinance", fake_fetch)
    result, provenance = collector.fetch_research_snapshot(
        "2330",
        "2014-01-01",
        "2014-01-03",
    )

    assert calls == [("2330.TW", "2014-01-01", "2014-01-04")]
    assert len(result) == 2
    assert provenance["provider"] == "Yahoo Finance via yfinance"
    assert provenance["auto_adjust"] is True
    assert provenance["repair"] is False
    assert "revised" in provenance["provider_data_mutability"]
    assert len(provenance["provider_payload_sha256"]) == 64
    assert len(collector._read_cache("2330.TW", "2014-01-01", "2014-01-03")) == 2
    assert collector._read_cache(
        "2330.TW", "2014-01-01", "2014-01-03"
    )["date"].tolist() == ["2014-01-02", "2014-01-03"]
