#!/usr/bin/env python3
"""
杜金龍台股量化預測系統 - 一鍵啟動入口腳本 (start.py)
預設啟動：機構級金融終端 (FastAPI + TradingView Lightweight Charts, Port 8000)
"""

import sys
import os
import argparse
import subprocess

def main():
    parser = argparse.ArgumentParser(description="杜金龍台股量化預測與分析系統 - 一鍵啟動器")
    parser.add_argument("--mode", type=str, choices=["fastapi", "streamlit", "test"], default="fastapi", 
                        help="啟動模式: fastapi (機構級終端, 預設 8000 埠) | streamlit (Streamlit Dashboard, 8501 埠) | test (全套單元測試)")
    parser.add_argument("--port", type=int, default=None, help="指定自訂通訊埠 (FastAPI 預設 8000, Streamlit 預設 8501)")
    args = parser.parse_args()

    # 確保以專案根目錄為工作路徑
    project_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_root)

    print("=" * 75)
    print(" [杜金龍台股量化預測與分析系統 - Anti-Gravity TU-Predictor System]")
    print("=" * 75)

    if args.mode == "test":
        print("\n[測試模式] 正在執行全套 pytest 單元測試集...")
        result = subprocess.run([sys.executable, "-m", "pytest", "tests/"])
        sys.exit(result.returncode)

    elif args.mode == "streamlit":
        port = args.port or 8501
        print(f"\n[Streamlit 模式] 正在啟動儀表板 (網址: http://localhost:{port})...")
        cmd = [sys.executable, "-m", "streamlit", "run", "src/ui_alert/dashboard.py", "--server.port", str(port)]
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\nStreamlit 服務已正常關閉。")

    else: # default: fastapi
        port = args.port or 8000
        print(f"\n[機構級金融終端] 正在啟動 FastAPI REST API 與 TradingView 前端...")
        print(f"-> 終端主頁: http://localhost:{port}")
        print(f"-> API 自動文檔: http://localhost:{port}/docs")
        print(f"-> 14:30 盤後自動推播排程已整合於背景運行中...\n")
        
        cmd = [sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", str(port), "--reload"]
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\nFastAPI 金融終端服務已正常關閉。")

if __name__ == "__main__":
    main()
