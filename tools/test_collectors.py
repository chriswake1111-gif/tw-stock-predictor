import sys
import os
import argparse
from datetime import datetime, timedelta

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.twse_collector import TWSECollector
from src.collectors.finmind_collector import FinMindCollector

def main():
    parser = argparse.ArgumentParser(description="Phase 1 數據採集與快取 CLI 測試工具")
    parser.add_argument("--symbol", type=str, default="2330", help="股票代號 (例如 2330 或 0000/TAIEX)")
    parser.add_argument("--days", type=int, default=30, help="要抓取的天數範疇")
    parser.add_argument("--db", type=str, default="data/cache.db", help="SQLite 快取檔案路徑")
    args = parser.parse_args()

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"啟動 Phase 1 數據採集診斷工具")
    print(f"測試標的: {args.symbol} | 區間: {start_date} ~ {end_date} | 快取 DB: {args.db}")
    print("=" * 60)

    # 1. 測試 TWSECollector (OHLCV)
    print("\n[1/3] 測試 TWSECollector (yfinance / SQLite Local-First K線抓取)...")
    twse_collector = TWSECollector(db_path=args.db)
    ohlcv_df = twse_collector.get_ohlcv(args.symbol, start_date=start_date, end_date=end_date)
    print(f"-> 獲取 {args.symbol} K 線筆數: {len(ohlcv_df)}")
    if not ohlcv_df.empty:
        print(ohlcv_df.tail(3).to_string())

    # 2. 測試 FinMindCollector (估值 PE/PB/殖利率)
    print("\n[2/3] 測試 FinMindCollector (估值 PE / PB / 殖利率)...")
    finmind_collector = FinMindCollector(db_path=args.db)
    val_df = finmind_collector.get_valuation(args.symbol, start_date=start_date, end_date=end_date)
    print(f"-> 獲取 {args.symbol} 估值資料筆數: {len(val_df)}")
    if not val_df.empty:
        print(val_df.tail(3).to_string())

    # 3. 測試 FinMindCollector (個股融資籌碼)
    print("\n[3/3] 測試 FinMindCollector (融資籌碼買賣超)...")
    margin_df = finmind_collector.get_margin_trading(args.symbol, start_date=start_date, end_date=end_date)
    print(f"-> 獲取 {args.symbol} 融資籌碼資料筆數: {len(margin_df)}")
    if not margin_df.empty:
        print(margin_df.tail(3).to_string())

    print("\n" + "=" * 60)
    print(" Phase 1 數據採集驗證流程結束！")
    print("=" * 60)

if __name__ == "__main__":
    main()
