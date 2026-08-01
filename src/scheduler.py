import os
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.analysis_service import analyze_symbol
from src.ui_alert.notifier import MultiChannelNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class AutoScheduler:
    """每日 14:30 盤後自動排程控制器 (APScheduler)"""

    def __init__(self, db_path: str = "data/cache.db", config_path: str = "config/config.yaml"):
        self.db_path = db_path
        self.config_path = config_path
        self.scheduler = BackgroundScheduler()
        self.notifier = MultiChannelNotifier(config_path=self.config_path)

    def run_daily_pipeline(self, symbol: str = "2330.TW"):
        """執行自動管線: 抓取新數據 -> 計算指標 -> 發送推播日報"""
        logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 啟動 14:30 盤後自動管線 (標的: {symbol})...")
        
        try:
            analysis, _ = analyze_symbol(
                symbol,
                db_path=self.db_path,
                config_path=self.config_path,
            )
            if analysis.get("status") != "available":
                logger.warning(f"管線分析 {symbol} 資料不足: {analysis.get('reason')}")
                return analysis

            realtime_wave = analysis["wave_analysis"]["realtime_confirmed"]
            targets = realtime_wave.get("targets", {})
            time_win = realtime_wave.get("time_window", {})
            sentiment = analysis["sentiment"]
            screener = analysis["two_lows_one_high"]

            msg = self.notifier.generate_daily_report(
                symbol=symbol,
                close_price=analysis["latest_price"],
                resonance_signal=analysis["is_resonance"],
                wave3_target=targets.get("wave3_1.618"),
                fib_window_msg=time_win.get("status_message", "資料不足"),
                volume_m1b_msg=sentiment.get("status_message", "資料不足"),
                screener_passed=screener.get("passed"),
            )
            
            notification_result = self.notifier.send_notification(msg)
            logger.info("14:30 盤後自動管線成功執行完畢!")
            return {
                "status": "success",
                "analysis_status": analysis["status"],
                "notification": notification_result,
            }

        except Exception as e:
            logger.error(f"盤後管線執行失敗: {str(e)}")
            return {"status": "error", "reason": str(e)}

    def start(self, cron_hour: int = 14, cron_minute: int = 30):
        """啟動背景 Cron 排程 (每週一至週五指定時間觸發)"""
        trigger = CronTrigger(day_of_week='mon-fri', hour=cron_hour, minute=cron_minute)
        self.scheduler.add_job(self.run_daily_pipeline, trigger, id="daily_tu_pipeline", replace_existing=True)
        self.scheduler.start()
        logger.info(f"自動排程器已啟動! 定時任務時間: 週一至週五 {cron_hour:02d}:{cron_minute:02d}")

    def stop(self):
        """停止排程器"""
        if self.scheduler.running:
            self.scheduler.shutdown()

    def get_job_info(self) -> dict:
        return {
            "cron_time": "14:30 (Mon-Fri)",
            "is_running": self.scheduler.running
        }
