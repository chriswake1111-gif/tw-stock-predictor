import os
import sqlite3
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class MarketTurnoverCollector:
    """
    TWSE＋TPEx 官方市場總成交金額 (traded_value) 採集器：
    1. 採集 TWSE 上市總成交金額與 TPEx 上櫃總成交金額，單位統一為新台幣元 (TWD)。
    2. Local-First SQLite 數據快取 (`data/cache.db` -> `market_turnover`)。
    3. 實作 `get_latest_available_turnover` 向前尋找最多 10 個自然日之最新有效交易日。
    4. 絕不使用 `taiex_df['volume']` 或股數充當成交金額。
    """

    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化市場總成交金額表"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_turnover (
                    trade_date TEXT PRIMARY KEY,
                    twse_value REAL,
                    tpex_value REAL,
                    total_value REAL
                )
            """)
            conn.commit()

    def get_latest_available_turnover(self, end_date: Optional[str] = None, lookback_days: int = 10) -> Dict[str, Any]:
        """
        向前尋找最多 lookback_days 個自然日內最新可用之 TWSE+TPEx 總成交金額 (TWD)
        :param end_date: 查詢截止日期 (格式 'YYYY-MM-DD'，預設當前日期)
        :param lookback_days: 向前追溯之最多自然日數 (預設 10 日)
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        
        for i in range(lookback_days):
            curr_date_str = (end_dt - timedelta(days=i)).strftime("%Y-%m-%d")
            res = self.get_total_market_turnover(curr_date_str)
            if res.get("status") == "available":
                return res

        return {
            "status": "insufficient_data",
            "reason": f"No market turnover available within {lookback_days} days before {end_date}",
            "market_turnover": None
        }

    def get_total_market_turnover(self, trade_date: str) -> Dict[str, Any]:
        """
        獲取指定日期之 TWSE+TPEx 總成交金額 (TWD)
        """
        # 1. 讀取 SQLite 快取
        cached = self._read_cache(trade_date)
        if cached:
            return {
                "status": "available",
                "market_turnover": {
                    "value": cached["total_value"],
                    "twse_value": cached["twse_value"],
                    "tpex_value": cached["tpex_value"],
                    "scope": "TWSE+TPEx",
                    "metric": "traded_value",
                    "unit": "TWD",
                    "trade_date": trade_date,
                    "source": ["TWSE Daily Trading Summary", "TPEx Daily Trading Summary"]
                }
            }

        # 2. 發起網路請求採集
        twse_val = self._fetch_twse_turnover(trade_date)
        tpex_val = self._fetch_tpex_turnover(trade_date)

        if twse_val is not None and tpex_val is not None:
            total_val = twse_val + tpex_val
            self._save_cache(trade_date, twse_val, tpex_val, total_val)
            return {
                "status": "available",
                "market_turnover": {
                    "value": total_val,
                    "twse_value": twse_val,
                    "tpex_value": tpex_val,
                    "scope": "TWSE+TPEx",
                    "metric": "traded_value",
                    "unit": "TWD",
                    "trade_date": trade_date,
                    "source": ["TWSE Daily Trading Summary", "TPEx Daily Trading Summary"]
                }
            }

        return {
            "status": "insufficient_data",
            "reason": f"Turnover data for {trade_date} is not available from official sources",
            "market_turnover": None
        }

    def _read_cache(self, trade_date: str) -> Optional[Dict[str, float]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT twse_value, tpex_value, total_value FROM market_turnover WHERE trade_date=?", (trade_date,))
            row = cursor.fetchone()
            if row:
                return {"twse_value": float(row[0]), "tpex_value": float(row[1]), "total_value": float(row[2])}
        return None

    def _save_cache(self, trade_date: str, twse_val: float, tpex_val: float, total_val: float):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO market_turnover (trade_date, twse_value, tpex_value, total_value)
                VALUES (?, ?, ?, ?)
            """, (trade_date, twse_val, tpex_val, total_val))
            conn.commit()

    def _fetch_twse_turnover(self, trade_date: str) -> Optional[float]:
        """從證交所 TWSE API 獲取當日總成交金額 (TWD)"""
        try:
            date_param = trade_date.replace("-", "")
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date_param}&response=json"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("stat") == "OK" and "data" in data:
                    # FMTQIK 回傳日期格式如 112/01/03，最後一筆比對
                    for row in reversed(data["data"]):
                        # 轉為民國年字串比對
                        dt = datetime.strptime(trade_date, "%Y-%m-%d")
                        roc_year = dt.year - 1911
                        roc_date_str = f"{roc_year}/{dt.month:02d}/{dt.day:02d}"
                        if row[0].strip() == roc_date_str:
                            # row[2] 為成交金額 (元)
                            turnover_str = row[2].replace(",", "").strip()
                            return float(turnover_str)
        except Exception as e:
            logger.warning(f"網路採集 TWSE {trade_date} 成交金額失敗: {str(e)}")
        return None

    def _fetch_tpex_turnover(self, trade_date: str) -> Optional[float]:
        """從櫃買中心 TPEx API 獲取當日總成交金額 (TWD)"""
        try:
            dt = datetime.strptime(trade_date, "%Y-%m-%d")
            roc_year = dt.year - 1911
            roc_date_str = f"{roc_year}/{dt.month:02d}/{dt.day:02d}"
            url = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_index/st41_result.php?l=zh-tw&d={roc_date_str}&s=0,asc"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "aaData" in data and len(data["aaData"]) > 0:
                    last_row = data["aaData"][-1]
                    # last_row[1] 為總成交金額 (元)
                    turnover_str = str(last_row[1]).replace(",", "").strip()
                    return float(turnover_str)
        except Exception as e:
            logger.warning(f"網路採集 TPEx {trade_date} 成交金額失敗: {str(e)}")
        return None
