import os
import yaml
import logging
import sqlite3
import pandas as pd
from typing import Dict, Any, Optional

from src.collectors.cbc_collector import CBCCollector

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class MarketSentimentEngine:
    """市場過熱與情緒溫度計演算引擎 (零假值與零 fallback 備援保護)"""

    def __init__(self, db_path: str = "data/cache.db", config_path: str = "config/config.yaml"):
        self.db_path = db_path
        self.config_path = config_path
        self.config = self._load_config()
        self.cbc_collector = CBCCollector(db_path=self.db_path, config_path=self.config_path)

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"載入 {self.config_path} 失敗: {str(e)}")
        return {}

    def check_turnover_m1b_overheat(
        self,
        market_turnover_twd: Optional[float] = None,
        m1b_twd: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        計算大盤成交金額 / M1B 頭部過熱比率 (turnover_m1b_ratio = market_turnover_twd / m1b_twd)
        若任一必要資料缺失，絕不使用假值或預設備援，一律回傳 status: "insufficient_data"。
        """
        threshold = self.config.get("sentiment", {}).get("volume_m1b_threshold", 0.020)
        m1b_contract = None

        # 1. 若未傳入 M1B，嘗試從 CBCCollector 獲取 Contract
        if m1b_twd is None:
            m1b_res = self.cbc_collector.get_latest_m1b()
            if m1b_res.get("status") == "available":
                m1b_contract = m1b_res
                m1b_billion = m1b_res["value"]
                m1b_twd = m1b_billion * 1e8

        # 2. 嚴格檢查資料完整性 (零假資料備援)
        if market_turnover_twd is None or market_turnover_twd <= 0 or m1b_twd is None or m1b_twd <= 0:
            return {
                "status": "insufficient_data",
                "reason": "Latest TWSE+TPEx market turnover or CBC M1B data is unavailable",
                "market_turnover_twd": market_turnover_twd,
                "m1b": m1b_contract or {"status": "insufficient_data", "value": None},
                "turnover_m1b_ratio": None,
                "is_overheat": None,
                "status_message": "資料不足：無法取得真實市場總成交金額或 M1B 數據"
            }

        # 3. 計算比率與過熱判定
        ratio = round(market_turnover_twd / m1b_twd, 6)
        is_overheat = bool(ratio >= threshold)

        return {
            "status": "available",
            "market_turnover_twd": market_turnover_twd,
            "m1b": m1b_contract or {
                "status": "available",
                "value": round(m1b_twd / 1e8, 2),
                "unit": "TWD_100_million",
                "source": "CBC"
            },
            "turnover_m1b_ratio": ratio,
            "threshold": threshold,
            "is_overheat": is_overheat,
            "status_message": f"大盤成交金額 / M1B 比率: {ratio*100:.2f}%" + 
                             (f" [觸發頭部過熱警戒! (≥ {threshold*100:.1f}%)]" if is_overheat else " [正常適中區間]")
        }

    def check_margin_leverage_heat(self, symbol: str = "^TWII") -> Dict[str, Any]:
        """全市場融資槓桿過熱溫度計"""
        threshold = self.config.get("sentiment", {}).get("margin_return_threshold", 0.08)
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_margin'")
                if not cursor.fetchone():
                    return {
                        "status": "insufficient_data",
                        "reason": "stock_margin table does not exist in SQLite",
                        "is_overheat": None,
                        "status_message": "融資數據表未初始化"
                    }

                query = "SELECT date, margin_balance FROM stock_margin WHERE stock_id=? ORDER BY date DESC LIMIT 60"
                df = pd.read_sql_query(query, conn, params=(symbol,))
                
            if df.empty or len(df) < 20:
                return {
                    "status": "insufficient_data",
                    "reason": "Margin data points fewer than 20 days",
                    "is_overheat": None,
                    "status_message": "融資數據點不足"
                }

            df.sort_values("date", inplace=True)
            recent_balance = float(df['margin_balance'].iloc[-1])
            past_balance = float(df['margin_balance'].iloc[0])
            
            growth_rate = (recent_balance - past_balance) / past_balance if past_balance > 0 else 0.0
            is_overheat = bool(growth_rate >= threshold)

            return {
                "status": "available",
                "recent_balance": recent_balance,
                "margin_growth_rate": round(growth_rate, 4),
                "threshold": threshold,
                "is_overheat": is_overheat,
                "status_message": f"融資餘額增長率: {growth_rate*100:.2f}%" + 
                                 (f" [過熱警戒! (≥ {threshold*100:.1f}%)]" if is_overheat else " [籌碼面正常]")
            }
        except Exception as e:
            logger.error(f"查詢融資熱度失敗: {str(e)}")
            return {
                "status": "insufficient_data",
                "reason": str(e),
                "is_overheat": None,
                "status_message": "融資數據查詢失敗"
            }
