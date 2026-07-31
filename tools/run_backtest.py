import sys
import os
import argparse
import pandas as pd

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.twse_collector import TWSECollector
from src.strategy.backtester import TuBacktester

def main():
    parser = argparse.ArgumentParser(description="Phase 4 & Refactored 策略回測工具")
    parser.add_argument("--symbol", type=str, default="2330.TW", help="回測標的代號 (例如 2330.TW 或 ^TWII)")
    parser.add_argument("--cash", type=float, default=1000000.0, help="初始回測資金 (預設 1,000,000 元)")
    args = parser.parse_args()

    print("=" * 75)
    print(f" [杜金龍 20/30/50 量化策略歷史回測] 標的: {args.symbol} | 初始資金: ${args.cash:,.0f} 元")
    print("=" * 75)

    collector = TWSECollector()
    df = collector.get_ohlcv(args.symbol, start_date="2020-01-01")

    if df.empty:
        print(f"錯誤: 無法獲取 {args.symbol} 的歷史 K 線數據")
        return

    backtester = TuBacktester(initial_cash=args.cash)
    res = backtester.run_backtest(df)

    if "error" in res:
        print(f"回測失敗: {res['error']}")
        return

    print(f"\n【績效統計結果】")
    print(f" - 初始資金: ${res['initial_cash']:,.0f} 元")
    print(f" - 最終資產: ${res['final_value']:,.0f} 元")
    print(f" - 累積總報酬率: {res['total_return_pct']}%")
    print(f" - 最大歷史回撤 (MDD): {res['max_drawdown_pct']}%")
    print(f" - 交易次數: {res['total_trades']} 次 | 勝率: {res['win_rate_pct']}%")
    print(f" - 夏普比率 (Sharpe Ratio): {res['sharpe_ratio']}")

    print(f"\n【精確交易紀錄】 (前 5 筆):")
    for log in res.get("execution_log", [])[:5]:
        print(f"   [{log['date']}] {log['action'].upper()} {log['size']} 股 @ ${log['price']:.2f} (Stage -> {log['stage_after']}, 手續費: ${log['commission']:.1f})")

    print("\n" + "=" * 75)
    print(" 回測診斷完成！")
    print("=" * 75)

if __name__ == "__main__":
    main()
