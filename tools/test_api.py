import sys
import os
import argparse
from fastapi.testclient import TestClient

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.main import app

def main():
    parser = argparse.ArgumentParser(description="FastAPI REST API 機構級端點 CLI 測試工具")
    parser.add_argument("--symbol", type=str, default="2330", help="測試代號 (例如 2330 或 0000)")
    args = parser.parse_args()

    print("=" * 70)
    print(f" [FastAPI REST API 機構級端點診斷工具] 測試標的: {args.symbol}")
    print("=" * 70)

    client = TestClient(app)

    # 1. 測試 /api/health
    print("\n[1/2] 測試 GET /api/health...")
    health_resp = client.get("/api/health")
    print(f"-> 狀態碼: {health_resp.status_code} | 回傳: {health_resp.json()}")

    # 2. 測試 /api/analysis/{symbol}
    print(f"\n[2/2] 測試 GET /api/analysis/{args.symbol}...")
    analysis_resp = client.get(f"/api/analysis/{args.symbol}")
    print(f"-> 狀態碼: {analysis_resp.status_code}")
    if analysis_resp.status_code == 200:
        data = analysis_resp.json()
        print(f"   |- 標的與收盤價: {data['symbol']} (${data['latest_price']} 元)")
        print(f"   |- 多空共振狀態: {'[亮燈發動]' if data['is_resonance'] else '[未發動]'}")
        print(f"   |- 浪 3 主升段目標價: ${data['wave_targets']['wave3_1.618']} 元")
        print(f"   |- TradingView K 線筆數: {len(data['kline_data'])} 筆 (最新日期: {data['kline_data'][-1]['time']})")
        print(f"   |- 均線扣抵天數預判數量: {len(data['ma_deductions'])} 組")

    print("\n" + "=" * 70)
    print(" FastAPI REST API 診斷完畢！")
    print("=" * 70)

if __name__ == "__main__":
    main()
