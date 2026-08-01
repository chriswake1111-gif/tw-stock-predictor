import pytest
import pandas as pd
from src.engine.valuation_eva import ValuationEVAEngine

def test_dog_and_master_valuation():
    engine = ValuationEVAEngine(config_path="config/config.yaml")
    
    # 給定預估 EPS = 40.0, PE_min=10, PE_mid=20, PE_max=25
    valuation = engine.calculate_dog_master_valuation(eps=40.0, pe_min=10.0, pe_mid=20.0, pe_max=25.0)
    
    assert valuation["cheap_price"] == 400.0
    assert valuation["fair_price"] == 800.0
    assert valuation["expensive_price"] == 1000.0

def test_dual_track_eps():
    engine = ValuationEVAEngine(config_path="config/config.yaml")
    
    # 軌道 1: 法人預估 EPS
    eps_track1 = engine.estimate_future_eps(institutional_eps=45.0)
    assert eps_track1 == 45.0
    
    # 軌道 2: 歷史 TTM EPS * (1 + 10%)
    eps_track2 = engine.estimate_future_eps(historical_ttm_eps=40.0, growth_rate=0.10)
    assert eps_track2 == 44.0

def test_negative_eps_is_not_applicable_to_pe_valuation():
    engine = ValuationEVAEngine(config_path="config/config.yaml")

    result = engine.calculate_dog_master_valuation(eps=-2.0)

    assert result["status"] == "not_applicable"
    assert result["cheap_price"] is None
    assert result["fair_price"] is None
    assert result["expensive_price"] is None

def test_eva_floor_valuation():
    engine = ValuationEVAEngine(config_path="config/config.yaml")
    
    # NOPAT = 100 億, Capital = 800 億, WACC = 7%
    # EVA = 100 - 800 * 0.07 = 100 - 56 = 44 億
    eva_result = engine.calculate_eva_floor(nopat=100.0, invested_capital=800.0, wacc=0.07, total_shares_billion=1.0)
    assert eva_result["eva_billion"] == 44.0
    assert "eva_floor_price" in eva_result

def test_two_lows_one_high_screener():
    engine = ValuationEVAEngine(config_path="config/config.yaml")
    
    # 符合二低一高 (PE=12 < 15, PB=1.2 < 1.5, Yield=0.05 > 0.04)
    res_pass = engine.screen_two_lows_one_high(pe=12.0, pb=1.2, yield_rate=0.05)
    assert res_pass["passed"] == True
    
    # 不符合
    res_fail = engine.screen_two_lows_one_high(pe=22.0, pb=1.8, yield_rate=0.02)
    assert res_fail["passed"] == False

    # FinMind 原始 3.5 表示 3.5%，正規化後為 0.035，應低於 4% 門檻。
    normalized_yield = 3.5 / 100.0
    res_below_yield = engine.screen_two_lows_one_high(
        pe=12.0, pb=1.2, yield_rate=normalized_yield
    )
    assert res_below_yield["passed"] == False

def test_breakout_reversal_pattern():
    engine = ValuationEVAEngine(config_path="config/config.yaml")
    
    # 造一個破底翻型態的 DataFrame
    # 創近 5 日新低 (80) 後，下一天放量 (5000 > 1000) 快速拉回收復原支撐 (87 > 82)
    dates = pd.date_range("2024-01-01", periods=15, freq="B")
    close = [100, 98, 96, 95, 94, 92, 90, 88, 85, 82, 80, 87, 91, 93, 95] # 80 創新低，87 放量翻上
    volume = [1000]*11 + [5000]*4
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": close, "volume": volume})
    
    reversal_series = engine.detect_breakout_reversal(df, lookback_days=5, recovery_days=3)
    assert isinstance(reversal_series, pd.Series)
    assert reversal_series.any() == True
