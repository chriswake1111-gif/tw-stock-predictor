import pytest
import pandas as pd
from datetime import datetime, timedelta
from src.engine.wave_fibonacci import WaveFibonacciEngine

def test_wave_targets_calculation():
    engine = WaveFibonacciEngine()
    targets = engine.calculate_wave_targets(p0=12629.0, p1=15475.0, p2=14001.0)
    
    assert "wave3_1.382" in targets
    assert "wave3_1.618" in targets
    assert targets["wave3_1.618"] == 18605.83

def test_no_repaint_pivot_prefix_invariance():
    """Prefix Invariance 測試：加入未來數據時，過去已發布之 confirmed_at 轉折絕不改寫"""
    engine = WaveFibonacciEngine()
    
    # 建立 100 天與 150 天合成數據
    dates_100 = [(datetime(2023, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(100)]
    prices_100 = [100.0 + (i % 10 if i % 20 < 10 else -(i % 10)) for i in range(100)]
    df_100 = pd.DataFrame({"date": dates_100, "open": prices_100, "high": [p+1 for p in prices_100], "low": [p-1 for p in prices_100], "close": prices_100})

    dates_150 = [(datetime(2023, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(150)]
    prices_150 = prices_100 + [prices_100[-1] + (i % 5) for i in range(50)]
    df_150 = pd.DataFrame({"date": dates_150, "open": prices_150, "high": [p+1 for p in prices_150], "low": [p-1 for p in prices_150], "close": prices_150})

    pivots_100 = engine.detect_confirmed_pivots(df_100, confirmation_bars=3)
    pivots_150 = engine.detect_confirmed_pivots(df_150, confirmation_bars=3)

    # 比對前 100 天之已發布轉折
    pivots_150_prefix = [p for p in pivots_150 if p.confirmed_at_index < 97]
    
    assert len(pivots_100) == len(pivots_150_prefix)
    for p100, p150 in zip(pivots_100, pivots_150_prefix):
        assert p100.pivot_price == p150.pivot_price
        assert p100.pivot_date == p150.pivot_date
        assert p100.confirmed_at == p150.confirmed_at

def test_realtime_time_window_respects_as_of_date():
    engine = WaveFibonacciEngine()
    dates = [(datetime(2023, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
    prices = [100.0 + (i % 10 if i % 20 < 10 else -(i % 10)) for i in range(60)]
    df = pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
    })

    as_of_date = dates[39]
    result = engine.get_realtime_confirmed_wave_params("TEST", df, as_of_date=as_of_date)

    assert result["status"] == "available"
    assert result["time_window"]["latest_date"] <= as_of_date
