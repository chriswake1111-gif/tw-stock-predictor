import pytest
from src.ui_alert.notifier import MultiChannelNotifier
from src.scheduler import AutoScheduler

def test_daily_report_generation():
    notifier = MultiChannelNotifier()
    
    report_text = notifier.generate_daily_report(
        symbol="2330.TW",
        close_price=925.0,
        resonance_signal=True,
        wave3_target=949.77,
        fib_window_msg="已歷經 55 天 [觸發轉折視窗]",
        volume_m1b_msg="成交金額比率 1.67% [正常]",
        screener_passed=True
    )
    
    assert "2330.TW" in report_text
    assert "925" in report_text
    assert "949.77" in report_text
    assert "多空共振" in report_text

def test_notifier_console_fallback():
    # 當沒有設定 Telegram / LINE Token 時，呼叫 send_notification 應走 Console 降級模式且不崩潰
    notifier = MultiChannelNotifier(telegram_token="", line_token="", discord_webhook="")
    res = notifier.send_notification("測試告警訊息")
    
    assert isinstance(res, dict)
    assert res.get("console_fallback") == True

def test_scheduler_init():
    scheduler = AutoScheduler()
    job_info = scheduler.get_job_info()
    assert isinstance(job_info, dict)
    assert "cron_time" in job_info
