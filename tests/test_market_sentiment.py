import pytest
import pandas as pd
from src.collectors.cbc_collector import CBCCollector
from src.engine.market_sentiment import MarketSentimentEngine

def test_cbc_collector_m1b_fallback(tmp_path):
    db_file = str(tmp_path / "test_cbc.db")
    collector = CBCCollector(db_path=db_file, config_path="config/config.yaml")
    
    m1b_val = collector.get_latest_m1b()
    assert isinstance(m1b_val, float)
    assert m1b_val > 0.0

def test_volume_m1b_overheat_signal():
    engine = MarketSentimentEngine(config_path="config/config.yaml")
    
    # 日成交量 6500 億，M1B = 270,000 億 => Ratio = 6500 / 270000 = 0.024 > 0.020 (過熱)
    result_overheat = engine.check_volume_m1b_overheat(daily_volume_billion=6500.0, m1b_billion=270000.0)
    assert result_overheat["is_overheat"] == True
    
    # 日成交量 3000 億 => Ratio = 3000 / 270000 = 0.011 < 0.020 (正常)
    result_normal = engine.check_volume_m1b_overheat(daily_volume_billion=3000.0, m1b_billion=270000.0)
    assert result_normal["is_overheat"] == False

def test_margin_leverage_heat():
    engine = MarketSentimentEngine(config_path="config/config.yaml")
    
    # 融資報酬率 10% > 8% (過熱)
    heat_over = engine.check_margin_leverage_heat(margin_return=0.10)
    assert heat_over["is_heat_warning"] == True
    
    # 融資報酬率 4% < 8% (安全)
    heat_safe = engine.check_margin_leverage_heat(margin_return=0.04)
    assert heat_safe["is_heat_warning"] == False
