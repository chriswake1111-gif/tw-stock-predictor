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

def test_twse_collector_fetch_ohlcv(temp_db):
    collector = TWSECollector(db_path=temp_db)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    
    df = collector.get_ohlcv("2330.TW", start_date=start_date, end_date=end_date)
    assert isinstance(df, pd.DataFrame)
    if not df.empty:
        required_cols = {"date", "open", "high", "low", "close", "volume", "symbol"}
        assert required_cols.issubset(df.columns)

def test_finmind_collector_valuation(temp_db):
    collector = FinMindCollector(db_path=temp_db)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    
    df = collector.get_valuation("2330", start_date=start_date, end_date=end_date)
    assert isinstance(df, pd.DataFrame)
    if not df.empty:
        required_cols = {"stock_id", "date"}
        assert required_cols.issubset(df.columns)

def test_finmind_collector_margin(temp_db):
    collector = FinMindCollector(db_path=temp_db)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    
    df = collector.get_margin_trading("2330", start_date=start_date, end_date=end_date)
    assert isinstance(df, pd.DataFrame)
    if not df.empty:
        required_cols = {"stock_id", "date"}
        assert required_cols.issubset(df.columns)
