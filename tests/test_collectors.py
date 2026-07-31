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
