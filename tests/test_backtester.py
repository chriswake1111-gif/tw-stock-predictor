import pytest
import pandas as pd
import numpy as np
from src.strategy.capital_allocation import CapitalAllocator
from src.strategy.backtester import TuBacktester

def test_capital_allocator_lots():
    allocator = CapitalAllocator(total_cash=1000000.0) # 100 萬台幣
    
    # 20% 建倉 = 200,000 元，若股價 100 元，1 張 = 100,000 元 => 2 張 (2000 股)
    lots_20 = allocator.calculate_position_size(price=100.0, target_ratio=0.20)
    assert lots_20 == 2000 # 2 張
    
    # 30% 建倉 = 300,000 元，若股價 100 元 => 3 張 (3000 股)
    lots_30 = allocator.calculate_position_size(price=100.0, target_ratio=0.30)
    assert lots_30 == 3000 # 3 張

def test_downward_gap_defense():
    allocator = CapitalAllocator()
    
    # 昨日收盤 100，今日開盤 98 (跌 2% > 1.5%) => 觸發跳空防禦，不准進場
    is_safe = allocator.check_gap_down_defense(open_price=98.0, prev_close=100.0, gap_threshold=0.015)
    assert is_safe == False
    
    # 昨日收盤 100，今日開盤 99.5 (跌 0.5% < 1.5%) => 安全進場
    is_safe_normal = allocator.check_gap_down_defense(open_price=99.5, prev_close=100.0, gap_threshold=0.015)
    assert is_safe_normal == True

def test_backtester_execution():
    # 產生 200 個交易日數據以滿足長天期均線 (SMA 144) 運算範疇
    dates = pd.date_range("2024-01-01", periods=200, freq="B")
    close_prices = np.linspace(500, 800, 200) # 連續多頭
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close_prices - 2.0,
        "high": close_prices + 5.0,
        "low": close_prices - 5.0,
        "close": close_prices,
        "volume": 10000
    })
    
    backtester = TuBacktester(initial_cash=1000000.0)
    results = backtester.run_backtest(df, symbol="2330.TW")
    
    assert isinstance(results, dict)
    assert "total_return_pct" in results
    assert "max_drawdown_pct" in results
    assert "win_rate_pct" in results
