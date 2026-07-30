import sys
import os
import argparse
from datetime import datetime, timedelta

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.twse_collector import TWSECollector
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.engine.ma_deduction import MADeductionEngine

def main():
    parser = argparse.ArgumentParser(description="Phase 2 杜金龍波浪與均線扣抵引擎 CLI 測試工具")
    parser.add_argument("--symbol", type=str, default="^TWII", help="股票代號 (如 ^TWII 加權指數或 2330.TW 台積電)")
    parser.add_argument("--db", type=str, default="data/cache.db", help="SQLite 快取檔案路徑")
    args = parser.parse_args()

    print("=" * 70)
    print(f" [Phase 2 核心計算引擎診斷工具] 標的: {args.symbol}")
    print("=" * 70)

    # 1. 數據準備 (使用 TWSECollector 載入歷史數據)
    collector = TWSECollector(db_path=args.db)
    start_date = (datetime.now() - timedelta(days=365*2)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    df = collector.get_ohlcv(args.symbol, start_date=start_date, end_date=end_date)
    if df.empty:
        print(f"錯誤: 無法載入 {args.symbol} 歷史 K 線數據")
        return

    # 2. 測試 WaveFibonacciEngine
    print("\n[1/2] 波浪理論與費波南希時間/空間算圖引擎...")
    wave_engine = WaveFibonacciEngine(config_path="config/config.yaml")
    params = wave_engine.get_symbol_wave_params(args.symbol)
    
    p0 = params.get("p0", 12629.0)
    p1 = params.get("p1", 15475.0)
    p2 = params.get("p2", 14001.0)
    pivot_date = params.get("pivot_date", "2022-10-25")

    targets = wave_engine.calculate_wave_targets(p0=p0, p1=p1, p2=p2)
    time_window = wave_engine.check_time_window(df, pivot_date=pivot_date, is_monthly=False)

    print(f" 基準錨定點位: P0(浪1底)={p0} | P1(浪1頂)={p1} | P2(浪2底)={p2}")
    print(" 波浪目標價推導清單:")
    print(f"   |- 浪 3 (1.382 滿足點): {targets['wave3_1.382']}")
    print(f"   |- 浪 3 (1.618 主升段): {targets['wave3_1.618']}")
    print(f"   |- 浪 3 (2.000 擴張段): {targets['wave3_2.000']}")
    print(f"   |- 浪 3 (2.618 強勢段): {targets['wave3_2.618']}")
    print(f"   |- 浪 5 (3.236 滿載點): {targets['wave5_3.236']}")
    
    print(f" 費波南希時間轉折視窗評估:")
    print(f"   |- {time_window['status_message']}")

    # 3. 測試 MADeductionEngine
    print("\n[2/2] 費氏均線群扣抵預判與多空共振檢測器...")
    ma_engine = MADeductionEngine(config_path="config/config.yaml")
    analyzed_df = ma_engine.calculate_ma_and_deductions(df)
    resonance_series = ma_engine.detect_resonance_signal(analyzed_df)
    analyzed_df["resonance_signal"] = resonance_series

    latest_row = analyzed_df.iloc[-1]
    print(f" 最新交易日 ({latest_row['date']}) 收盤價: {latest_row['close']}")
    print(" 均線扣抵斜率與高低預判:")
    for period in [8, 13, 21, 55, 144]:
        sma_val = round(latest_row[f"SMA_{period}"], 2)
        deduct_val = round(latest_row[f"deduct_val_{period}"], 2)
        slope_up = latest_row[f"ma_slope_up_{period}"]
        slope_str = "▲ (向上支撐)" if slope_up else "▼ (下彎壓力)"
        print(f"   |- SMA {period:3d}: {sma_val:8.2f} | 扣抵值: {deduct_val:8.2f} | 方向: {slope_str}")

    is_resonance = latest_row["resonance_signal"]
    print(f" 今日多空共振攻擊訊號狀態: {'【亮燈 ! 多空共振發動】' if is_resonance else '【未觸發 (區間整理/分歧)】'}")

    print("\n" + "=" * 70)
    print(" Phase 2 核心計算引擎診斷完成！")
    print("=" * 70)

if __name__ == "__main__":
    main()
