import os
import pytest
import pandas as pd
from datetime import datetime, timedelta
from src.engine.wave_fibonacci import WaveFibonacciEngine

def test_wave_target_prices_calculation():
    engine = WaveFibonacciEngine(config_path="config/config.yaml")
    
    # 測試依給定 P0=12629, P1=15475, P2=14001 計算浪 3 主升段與浪 5 目標價
    targets = engine.calculate_wave_targets(p0=12629.0, p1=15475.0, p2=14001.0)
    
    assert "wave3_1.382" in targets
    assert "wave3_1.618" in targets
    assert "wave3_2.000" in targets
    assert "wave3_2.618" in targets
    assert "wave5_1.000" in targets
    
    wave1_diff = 15475.0 - 12629.0 # 2846
    # Wave 3 (1.618) = P2 + wave1_diff * 1.618 = 14001 + 2846 * 1.618 = 18605.828
    expected_w3_1618 = round(14001.0 + wave1_diff * 1.618, 2)
    assert round(targets["wave3_1.618"], 2) == expected_w3_1618

def test_wave_config_loading():
    engine = WaveFibonacciEngine(config_path="config/config.yaml")
    params = engine.get_symbol_wave_params("^TWII")
    
    assert params["p0"] == 12629.0
    assert params["p1"] == 15475.0

def test_fibonacci_time_window_daily():
    engine = WaveFibonacciEngine(config_path="config/config.yaml")
    
    # 造 100 個交易日數據
    dates = pd.date_range(end=datetime.now(), periods=100, freq='B')
    df = pd.DataFrame({'date': dates.strftime('%Y-%m-%d'), 'close': 100.0})
    
    pivot_date = df['date'].iloc[0] # 第 0 天
    result_8 = engine.check_time_window(df, pivot_date=pivot_date, is_monthly=False)
    assert isinstance(result_8, dict)
    assert "is_in_window" in result_8
    assert "elapsed_units" in result_8
    assert result_8["elapsed_units"] == 100
