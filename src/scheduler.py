import os
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.collectors.twse_collector import TWSECollector
from src.collectors.finmind_collector import FinMindCollector
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.engine.ma_deduction import MADeductionEngine
from src.engine.valuation_eva import ValuationEVAEngine
from src.engine.market_sentiment import MarketSentimentEngine
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
            # 1. 抓取最新數據
            twse = TWSECollector(db_path=self.db_path)
            finmind = FinMindCollector(db_path=self.db_path)
            
            df = twse.get_ohlcv(symbol)
            if df.empty:
                logger.warning(f"管線獲取 {symbol} K 線數據為空")
                return

            latest_row = df.iloc[-1]
            close_price = latest_row["close"]

            # 2. 計算波浪與均線扣抵
            wave_engine = WaveFibonacciEngine(config_path=self.config_path)
            ma_engine = MADeductionEngine(config_path=self.config_path)
            
            targets = wave_engine.calculate_wave_targets(p0=370.0, p1=546.0, p2=489.0)
            time_win = wave_engine.check_time_window(df, pivot_date="2022-10-25")
            
            analyzed_df = ma_engine.calculate_ma_and_deductions(df)
            resonance_series = ma_engine.detect_resonance_signal(analyzed_df)
            is_resonance = bool(resonance_series.iloc[-1])

            # 3. 計算估值與市場熱度
            val_engine = ValuationEVAEngine(config_path=self.config_path)
            sentiment_engine = MarketSentimentEngine(db_path=self.db_path, config_path=self.config_path)

            val_df = finmind.get_valuation("2330")
            screener_passed = False
            if not val_df.empty:
                last_val = val_df.iloc[-1]
                screener_res = val_engine.screen_two_lows_one_high(
                    pe=last_val.get("pe", 0), 
                    pb=last_val.get("pb", 0), 
                    yield_rate=last_val.get("yield_rate", 0)
                )
                screener_passed = screener_res["passed"]

            vol_overheat = sentiment_engine.check_volume_m1b_overheat(daily_volume_billion=4500.0)

            # 4. 生成簡報並發送推播
            msg = self.notifier.generate_daily_report(
                symbol=symbol,
                close_price=close_price,
                resonance_signal=is_resonance,
                wave3_target=targets["wave3_1.618"],
                fib_window_msg=time_win["status_message"],
                volume_m1b_msg=vol_overheat["status_message"],
                screener_passed=screener_passed
            )
            
            self.notifier.send_notification(msg)
            logger.info("14:30 盤後自動管線成功執行完畢!")

        except Exception as e:
            logger.error(f"盤後管線執行失敗: {str(e)}")

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
