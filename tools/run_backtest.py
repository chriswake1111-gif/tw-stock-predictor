import sys
import os
import argparse
from datetime import datetime, timedelta

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.twse_collector import TWSECollector
from src.strategy.backtester import TuBacktester

def main():
    parser = argparse.ArgumentParser(description="Phase 4 杜金龍交易策略與 Backtrader 回測 CLI 工具")
    parser.add_argument("--symbol", type=str, default="2330.TW", help="回測股票代號 (如 ^TWII 加權指數或 2330.TW 台積電)")
    parser.add_argument("--years", type=int, default=5, help="歷史回測年限範疇")
    parser.add_argument("--cash", type=float, default=1000000.0, help="初始資金 (預設 1,000,000 元)")
    parser.add_argument("--db", type=str, default="data/cache.db", help="SQLite 快取路徑")
    args = parser.parse_args()

    print("=" * 75)
    print(f" [Phase 4 杜金龍量化交易策略 Backtrader 回測診斷工具]")
    print(f" 測試標的: {args.symbol} | 回測區間: 近 {args.years} 年 | 初始資金: ${args.cash:,.0f} 元")
    print("=" * 75)

    # 1. 抓取歷史 K 線數據
    collector = TWSECollector(db_path=args.db)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365 * args.years)).strftime("%Y-%m-%d")
    
    df = collector.get_ohlcv(args.symbol, start_date=start_date, end_date=end_date)
    if df.empty:
        print(f"錯誤: 無法獲取 {args.symbol} 歷史數據")
        return

    # 2. 執行 Backtrader 事件驅動策略回測
    backtester = TuBacktester(initial_cash=args.cash, config_path="config/config.yaml")
    results = backtester.run_backtest(df, symbol=args.symbol)

    print("\n" + "=" * 75)
    print(f" 策略歷史回測戰績報告 ({args.symbol}):")
    print("=" * 75)
    print(f"   |- 初始資產: ${results['initial_cash']:,.2f} 元")
    print(f"   |- 最終資產: ${results['final_value']:,.2f} 元")
    print(f"   |- 總投資報酬率 (Total Return): {results['total_return_pct']}%")
    print(f"   |- 交易勝率 (Win Rate): {results['win_rate_pct']}%  ({results['won_trades']}/{results['total_trades']} 贏局)")
    print(f"   |- 最大回撤 (MDD): {results['max_drawdown_pct']}%")
    print(f"   |- 夏普比率 (Sharpe Ratio): {results['sharpe_ratio']}")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    main()
