import sys
import os
import argparse
import subprocess

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def main():
    parser = argparse.ArgumentParser(description="杜金龍台股量化預測系統 - CLI 啟動與診斷工具")
    parser.add_argument("--mode", type=str, choices=["fastapi", "streamlit", "test", "backtest"], default="fastapi",
                        help="啟動模式: fastapi | streamlit | test | backtest")
    parser.add_argument("--port", type=int, default=8000, help="埠號")
    args = parser.parse_args()

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(project_root)

    print("=" * 70)
    print(" [杜金龍台股量化預測系統 - CLI 啟動與診斷輔具]")
    print("=" * 70)

    if args.mode == "test":
        print("執行 pytest 單元測試集...")
        subprocess.run([sys.executable, "-m", "pytest", "tests/"])
    elif args.mode == "backtest":
        print("執行 5 年歷史回測診斷...")
        subprocess.run([sys.executable, "tools/run_backtest.py"])
    elif args.mode == "streamlit":
        print(f"啟動 Streamlit Dashboard (Port {args.port})...")
        subprocess.run([sys.executable, "-m", "streamlit", "run", "src/ui_alert/dashboard.py", "--server.port", str(args.port)])
    else:
        print(f"啟動 FastAPI 機構級金融終端 (Port {args.port})...")
        subprocess.run([sys.executable, "-m", "uvicorn", "src.api.main:app", "--port", str(args.port), "--reload"])

if __name__ == "__main__":
    main()
