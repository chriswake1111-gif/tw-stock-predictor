import pytest
import pandas as pd
import numpy as np
from src.engine.ma_deduction import MADeductionEngine

def test_ma_deduction_vectorized_slope():
    engine = MADeductionEngine()
    
    # 建立測試資料：連續上漲 30 天
    dates = pd.date_range(start="2024-01-01", periods=30, freq="B")
    close_prices = np.linspace(100, 200, 30)
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": close_prices})
    
    analyzed_df = engine.calculate_ma_and_deductions(df)
    
    # 測試是否包含 SMA 8, 13, 21, 55, 144, 233
    for period in [8, 13, 21, 55]:
        assert f"SMA_{period}" in analyzed_df.columns
        assert f"deduct_val_{period}" in analyzed_df.columns
        assert f"ma_slope_up_{period}" in analyzed_df.columns
    
    # 因為是持續上漲，第 8 天之後 SMA_8 斜率應全部向上 (True)
    valid_rows = analyzed_df.iloc[8:]
    assert valid_rows["ma_slope_up_8"].all() == True

def test_resonance_signal():
    engine = MADeductionEngine()
    
    # 建立強烈多頭發散資料
    dates = pd.date_range(start="2024-01-01", periods=300, freq="B")
    close_prices = np.linspace(100, 1000, 300)
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": close_prices})
    
    analyzed_df = engine.calculate_ma_and_deductions(df)
    resonance_series = engine.detect_resonance_signal(analyzed_df)
    
    assert isinstance(resonance_series, pd.Series)
    # 多頭極度強烈情況下，最後一天必須觸發多空共振 (True)
    assert resonance_series.iloc[-1] == True
