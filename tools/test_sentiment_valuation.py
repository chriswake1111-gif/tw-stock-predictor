import sys
import os
import argparse

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.collectors.finmind_collector import FinMindCollector
from src.collectors.cbc_collector import CBCCollector
from src.engine.valuation_eva import ValuationEVAEngine
from src.engine.market_sentiment import MarketSentimentEngine

def main():
    parser = argparse.ArgumentParser(description="Phase 3 基本面估值與市場熱度 CLI 測試工具")
    parser.add_argument("--symbol", type=str, default="2330", help="個股代號 (預設 2330 台積電)")
    parser.add_argument("--db", type=str, default="data/cache.db", help="SQLite 快取路徑")
    args = parser.parse_args()

    print("=" * 70)
    print(f" [Phase 3 基本面估值與市場熱度診斷工具] 測試個股: {args.symbol}")
    print("=" * 70)

    # 1. 估值與 EVA 模型診斷
    print("\n[1/2] 主人與小狗估值模型 & EVA 價值底盤評估...")
    val_engine = ValuationEVAEngine(config_path="config/config.yaml")
    finmind = FinMindCollector(db_path=args.db)

    # 以台積電為例：假定歷史 TTM EPS = 42.0 元，成長率 10% => 預估未來 EPS = 46.2 元
    estimated_eps = val_engine.estimate_future_eps(historical_ttm_eps=42.0)
    dog_valuation = val_engine.calculate_dog_master_valuation(eps=estimated_eps)

    print(f" 預估未來一年 EPS: {estimated_eps} 元 (採用歷史 TTM * 1.1 雙軌備援)")
    print(" 估值價位區間:")
    print(f"   |- 便宜價 (10x PE): {dog_valuation['cheap_price']} 元")
    print(f"   |- 合理價 (20x PE): {dog_valuation['fair_price']} 元")
    print(f"   |- 昂貴價 (25x PE): {dog_valuation['expensive_price']} 元")

    # EVA 價值底盤計算 (台積電 NOPAT 3500 億，資本 15000 億，WACC 7%，股數 259 億股)
    eva_result = val_engine.calculate_eva_floor(nopat=3500.0, invested_capital=15000.0, total_shares_billion=259.0)
    print(" EVA 長線價值底盤推算:")
    print(f"   |- NOPAT: {eva_result['nopat_billion']} 億元 | WACC: {eva_result['wacc']*100:.1f}%")
    print(f"   |- 創造 EVA 經濟附加價值: {eva_result['eva_billion']} 億元")
    print(f"   |- 強支撐長線價值底盤價: {eva_result['eva_floor_price']} 元")

    # 二低一高篩選
    val_df = finmind.get_valuation(args.symbol)
    if not val_df.empty:
        latest = val_df.iloc[-1]
        pe = latest.get("pe", 0.0)
        pb = latest.get("pb", 0.0)
        yield_rate = latest.get("yield_rate", 0.0)
        screen_res = val_engine.screen_two_lows_one_high(pe=pe, pb=pb, yield_rate=yield_rate)
        print(f" 二低一高選股器檢核 (當前 PE={pe:.2f}, PB={pb:.2f}, 殖利率={yield_rate:.2f}%):")
        print(f"   |- 評級判定: {'【符合二低一高優質標的】' if screen_res['passed'] else '【未完全符合 (如 PE/PB 較高)】'}")

    # 2. 市場熱度與槓桿溫度計
    print("\n[2/2] 總體籌碼與大盤頭部過熱溫度計...")
    sentiment_engine = MarketSentimentEngine(db_path=args.db, config_path="config/config.yaml")

    # 測試 M1B 成交量過熱 (假設當前台股單日總成交量 4500 億元)
    vol_result = sentiment_engine.check_volume_m1b_overheat(daily_volume_billion=4500.0)
    print(f" 大盤天量與 M1B 過熱警戒:")
    print(f"   |- {vol_result['status_message']}")

    # 測試融資槓桿過熱 (假設目前融資報酬率 6%)
    margin_heat = sentiment_engine.check_margin_leverage_heat(margin_return=0.06)
    print(f" 全市場融資槓桿過熱評估:")
    print(f"   |- {margin_heat['status_message']}")

    print("\n" + "=" * 70)
    print(" Phase 3 基本面估值與市場熱度診斷完成！")
    print("=" * 70)

if __name__ == "__main__":
    main()
