import pytest
from src.engine.market_sentiment import MarketSentimentEngine

def test_cbc_collector_m1b_fallback():
    engine = MarketSentimentEngine(config_path="config/config.yaml")
    assert engine.cbc_collector.default_m1b == 270000.0

def test_turnover_m1b_overheat_signal():
    engine = MarketSentimentEngine(config_path="config/config.yaml")
    
    # 450,000,000,000 TWD / 27,000,000,000,000 TWD = 0.016667 < 0.020 (正常)
    res_normal = engine.check_turnover_m1b_overheat(market_turnover_twd=450000000000.0, m1b_twd=27000000000000.0)
    assert res_normal["status"] == "available"
    assert res_normal["is_overheat"] == False

    # 650,000,000,000 TWD / 27,000,000,000,000 TWD = 0.024074 >= 0.020 (過熱)
    res_over = engine.check_turnover_m1b_overheat(market_turnover_twd=650000000000.0, m1b_twd=27000000000000.0)
    assert res_over["status"] == "available"
    assert res_over["is_overheat"] == True

    # 數據不足測試 (零假數據備援)
    res_missing = engine.check_turnover_m1b_overheat(market_turnover_twd=None, m1b_twd=None)
    assert res_missing["status"] == "insufficient_data"
    assert res_missing["turnover_m1b_ratio"] is None

def test_margin_leverage_heat():
    engine = MarketSentimentEngine(config_path="config/config.yaml")
    heat_res = engine.check_margin_leverage_heat()
    assert "status" in heat_res
