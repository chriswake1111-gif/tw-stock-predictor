import os
import json
import pytest
from unittest.mock import patch, MagicMock
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

def test_market_turnover_official_json_fixtures(temp_db):
    """使用官方 JSON Payload Fixture 驗證 TWSE 與 TPEx 成交金額解析"""
    collector = MarketTurnoverCollector(db_path=temp_db)

    with open("tests/fixtures/twse_fmtqik_response.json", "r", encoding="utf-8") as f:
        twse_fixture = json.load(f)

    with open("tests/fixtures/tpex_st41_response.json", "r", encoding="utf-8") as f:
        tpex_fixture = json.load(f)

    with patch("requests.get") as mock_get:
        # Mock TWSE & TPEx HTTP responses
        mock_resp_twse = MagicMock()
        mock_resp_twse.status_code = 200
        mock_resp_twse.json.return_value = twse_fixture

        mock_resp_tpex = MagicMock()
        mock_resp_tpex.status_code = 200
        mock_resp_tpex.json.return_value = tpex_fixture

        mock_get.side_effect = [mock_resp_twse, mock_resp_tpex]

        twse_val = collector._fetch_twse_turnover("2026-07-30")
        tpex_val = collector._fetch_tpex_turnover("2026-07-30")

        assert twse_val == 380000000000.0
        assert tpex_val == 90000000000.0


def test_v2_official_openapi_parsing_and_partial_persistence(temp_db):
    collector = MarketTurnoverCollector(db_path=temp_db)
    with open("tests/fixtures/twse_fmtqik_openapi.json", encoding="utf-8") as source:
        twse = json.load(source)
    with open("tests/fixtures/tpex_daily_trading_index_openapi.json", encoding="utf-8") as source:
        tpex = json.load(source)
    assert collector.parse_twse_openapi(twse, "2026-07-30") == 380_000_000_000
    assert collector.parse_tpex_openapi(tpex, "2026-07-30") == 90_000_000_000
    complete = collector.import_official_turnover(
        "2026-07-30", twse, tpex,
        "2026-07-30T09:00:00+08:00", "2026-07-30T09:01:00+08:00",
        ingested_at="2026-07-30T09:02:00+08:00",
    )
    assert complete["status"] == "available"
    assert complete["total_turnover_twd"] == 470_000_000_000
    partial = collector.import_official_turnover(
        "2026-07-31", [{"Date": "115/07/31", "TradeValue": "1,000"}], [],
        "2026-07-31T09:00:00+08:00", "2026-07-31T09:01:00+08:00",
        ingested_at="2026-07-31T09:02:00+08:00",
    )
    assert partial["status"] == "partial"
    assert partial["total_turnover_twd"] is None
