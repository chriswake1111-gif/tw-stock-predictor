import os
import pytest
from src.collectors.market_turnover_collector import MarketTurnoverCollector

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_turnover.db"
    return str(db_file)

def test_market_turnover_collector_init(temp_db):
    collector = MarketTurnoverCollector(db_path=temp_db)
    assert os.path.exists(temp_db)

def test_market_turnover_collector_cache(temp_db):
    collector = MarketTurnoverCollector(db_path=temp_db)
    trade_date = "2026-07-30"
    
    # 手動寫入測試快取
    collector._save_cache(trade_date, 350000000000.0, 100000000000.0, 450000000000.0)
    
    res = collector.get_total_market_turnover(trade_date)
    assert res["status"] == "available"
    assert res["market_turnover"]["value"] == 450000000000.0
    assert res["market_turnover"]["unit"] == "TWD"
    assert res["market_turnover"]["scope"] == "TWSE+TPEx"

def test_market_turnover_latest_available(temp_db):
    collector = MarketTurnoverCollector(db_path=temp_db)
    collector._save_cache("2026-07-28", 300000000000.0, 80000000000.0, 380000000000.0)
    
    # 向前搜尋最近交易日
    res = collector.get_latest_available_turnover(end_date="2026-07-30", lookback_days=5)
    assert res["status"] == "available"
    assert res["market_turnover"]["trade_date"] == "2026-07-28"
    assert res["market_turnover"]["value"] == 380000000000.0
