import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from src.strategy.backtester import TuBacktester, TuStrategy, TWSalesTaxCommissionScheme
from src.strategy.capital_allocation import CapitalAllocator

def generate_synthetic_ohlcv(days=300, start_price=100.0, trend='up'):
    """產出語意測試用的合成 K 線 DataFrame"""
    dates = [(datetime(2023, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    prices = [start_price]
    
    for i in range(1, days):
        if trend == 'up':
            change = np.random.uniform(0.1, 1.5)
        elif trend == 'down':
            change = np.random.uniform(-1.5, -0.1)
        else:
            change = np.random.uniform(-1.0, 1.0)
        prices.append(max(10.0, prices[-1] + change))

    df = pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p + 1.0 for p in prices],
        "low": [p - 1.0 for p in prices],
        "close": prices,
        "volume": [10000 + i*10 for i in range(days)]
    })
    return df

def test_capital_allocator_order_size():
    allocator = CapitalAllocator(lot_size=1000, cash_buffer_rate=0.005)
    
    # 初始資金 1,000,000 元，單價 100 元，Stage 1 (20% Target = 200,000 元)
    size1 = allocator.calculate_order_size(
        price=100.0,
        target_cumulative_ratio=0.20,
        base_value=1000000.0,
        current_position_size=0,
        available_cash=1000000.0
    )
    # 200,000 / (100 * 1000) = 2 張 = 2,000 股
    assert size1 == 2000

    # Stage 2 (50% Cumulative Target = 500,000 元)，增量需求 = 500,000 - 200,000 = 300,000 元 = 3 張 = 3,000 股
    size2 = allocator.calculate_order_size(
        price=100.0,
        target_cumulative_ratio=0.50,
        base_value=1000000.0,
        current_position_size=2000,
        available_cash=798000.0
    )
    assert size2 == 3000

def test_backtester_execution():
    df = generate_synthetic_ohlcv(days=300, start_price=100.0, trend='up')
    backtester = TuBacktester(initial_cash=1000000.0)
    res = backtester.run_backtest(df)

    assert "final_value" in res
    assert "total_return_pct" in res
    assert "execution_log" in res
    assert res["mode"] == "historical_backtest_only"
    assert res["execution_capability"] == "simulated_orders_only"
    assert "average_capital_utilization_pct" in res
    
    # 驗證交易日誌結構與 Stage 流轉
    log = res["execution_log"]
    if len(log) > 0:
        first_trade = log[0]
        assert "date" in first_trade
        assert "action" in first_trade
        assert "stage_after" in first_trade
        assert "commission" in first_trade
        assert "signal_date" in first_trade
        assert "execution_date" in first_trade
        if first_trade["action"] != "sell_terminal_liquidation":
            assert first_trade["signal_date"] < first_trade["execution_date"]


def test_backtester_rejects_invalid_sensitivity_parameters():
    with pytest.raises(ValueError, match="持倉比例"):
        TuBacktester(strategy_params={"stage1_ratio": 0.7, "stage2_ratio": 0.5})

    with pytest.raises(ValueError, match="拉回參數"):
        TuBacktester(
            strategy_params={"pullback_min_pct": 0.2, "pullback_max_pct": 0.1}
        )
