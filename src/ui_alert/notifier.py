import os
import yaml
import logging
import requests
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class MultiChannelNotifier:
    """
    多軌盤後自動推播器：
    1. Telegram Bot
    2. LINE Messaging API (LINE 官方帳號 Push Message)
    3. Discord Webhook
    若 Token / Webhook 未設定，自動回退 Console 日誌，絕不中斷系統。
    """

    def __init__(
        self, 
        telegram_token: Optional[str] = None, 
        telegram_chat_id: Optional[str] = None,
        line_token: Optional[str] = None,
        line_user_id: Optional[str] = None,
        discord_webhook: Optional[str] = None,
        config_path: str = "config/config.yaml"
    ):
        self.config_path = config_path
        cfg = self._load_config()
        
        self.telegram_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN", cfg.get("notifier", {}).get("telegram_token", ""))
        self.telegram_chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", cfg.get("notifier", {}).get("telegram_chat_id", ""))
        self.line_token = line_token or os.getenv("LINE_CHANNEL_ACCESS_TOKEN", cfg.get("notifier", {}).get("line_token", ""))
        self.line_user_id = line_user_id or os.getenv("LINE_USER_ID", cfg.get("notifier", {}).get("line_user_id", ""))
        self.discord_webhook = discord_webhook or os.getenv("DISCORD_WEBHOOK_URL", cfg.get("notifier", {}).get("discord_webhook", ""))

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"讀取 {self.config_path} 失敗: {str(e)}")
        return {}

    def generate_daily_report(
        self,
        symbol: str,
        close_price: float,
        resonance_signal: bool,
        wave3_target: Optional[float],
        fib_window_msg: str,
        volume_m1b_msg: str,
        screener_passed: Optional[bool]
    ) -> str:
        """格式化產出 14:30 盤後精簡報告文字"""
        resonance_str = "[亮燈: 多空共振發動]" if resonance_signal else "[中立: 未觸發 (區間整理)]"
        if screener_passed is None:
            screener_str = "[資料不足: 尚無法評估]"
        else:
            screener_str = "[符合: 二低一高研究條件]" if screener_passed else "[未符合: 研究條件未全數成立]"
        wave_target_str = f"${wave3_target} 元" if wave3_target is not None else "資料不足"

        report = f"""
====================================
【台股市場研究與決策支援】14:30 盤後觀察
====================================
標的代號: {symbol}
今日收盤價: ${close_price} 元

多空共振狀態: {resonance_str}
浪 3 研究推導目標價: {wave_target_str}
費氏時間轉折視窗: {fib_window_msg}
大盤頭部過熱評估: {volume_m1b_msg}
二低一高選股評估: {screener_str}
====================================
僅供個人研究與決策參考，不構成投資建議或真實委託指令。
"""
        return report.strip()

    def send_notification(self, message: str) -> Dict[str, Any]:
        """多軌推播發送 (Telegram / LINE Bot / Discord Webhook)"""
        sent_channels = []
        
        # 1. Telegram Bot
        if self.telegram_token and self.telegram_chat_id:
            try:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                resp = requests.post(url, json={"chat_id": self.telegram_chat_id, "text": message}, timeout=10)
                if resp.status_code == 200:
                    sent_channels.append("Telegram")
            except Exception as e:
                logger.error(f"Telegram 推播失敗: {str(e)}")

        # 2. LINE Messaging API (Push Message)
        if self.line_token and self.line_user_id:
            try:
                url = "https://api.line.me/v2/bot/message/push"
                headers = {
                    "Authorization": f"Bearer {self.line_token}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "to": self.line_user_id,
                    "messages": [{"type": "text", "text": message}]
                }
                resp = requests.post(url, headers=headers, json=payload, timeout=10)
                if resp.status_code == 200:
                    sent_channels.append("LINE Bot")
            except Exception as e:
                logger.error(f"LINE Bot 推播失敗: {str(e)}")

        # 3. Discord Webhook
        if self.discord_webhook:
            try:
                resp = requests.post(self.discord_webhook, json={"content": message}, timeout=10)
                if resp.status_code in [200, 204]:
                    sent_channels.append("Discord")
            except Exception as e:
                logger.error(f"Discord Webhook 推播失敗: {str(e)}")

        # 4. 若全無 Token 或發送失敗，自動降級為 Console 日誌 (Console Fallback)
        if not sent_channels:
            logger.info(f"\n[Console Fallback 推播輸出]:\n{message}")
            return {"status": "success", "console_fallback": True, "channels": ["Console"]}

        logger.info(f"多軌推播成功發送至: {', '.join(sent_channels)}")
        return {"status": "success", "console_fallback": False, "channels": sent_channels}
