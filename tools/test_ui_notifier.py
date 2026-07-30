import sys
import os
import argparse

# 將專案根目錄加入 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ui_alert.notifier import MultiChannelNotifier
from src.scheduler import AutoScheduler

def main():
    parser = argparse.ArgumentParser(description="Phase 5 視覺化 UI 與多軌推播器 CLI 測試工具")
    parser.add_argument("--symbol", type=str, default="2330.TW", help="測試推播個股代號")
    args = parser.parse_args()

    print("=" * 70)
    print(" [Phase 5 多軌自動推播與盤後排程診斷工具]")
    print("=" * 70)

    # 1. 測試 MultiChannelNotifier 多軌推播與 Console 降級
    print("\n[1/2] 測試 MultiChannelNotifier 盤後日報格式化與多軌推播...")
    notifier = MultiChannelNotifier(config_path="config/config.yaml")

    report_text = notifier.generate_daily_report(
        symbol=args.symbol,
        close_price=925.0,
        resonance_signal=True,
        wave3_target=949.77,
        fib_window_msg="自基準日 2022-10-25 起已歷經 55 個交易日 [觸發 55 日時間轉折視窗!]",
        volume_m1b_msg="大盤成交金額 4500 億 / M1B 270000 億 (比率: 1.67%) [正常]",
        screener_passed=True
    )

    print("\n" + report_text + "\n")
    push_res = notifier.send_notification(report_text)
    print(f"-> 推播結果: {push_res}")

    # 2. 測試 AutoScheduler 自動化排程器與單次執行
    print("\n[2/2] 測試 AutoScheduler 14:30 自動化排程器即時觸發...")
    scheduler = AutoScheduler(config_path="config/config.yaml")
    info = scheduler.get_job_info()
    print(f"-> 排程器狀態: Cron 定時為 {info['cron_time']} | 運行狀態: {info['is_running']}")

    print("\n" + "=" * 70)
    print(" Phase 5 UI 與多軌推播診斷完成！")
    print("=" * 70)

if __name__ == "__main__":
    main()
