import os
import sqlite3
import logging
import requests
import pandas as pd
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from src.domain.liquidity import MarketTurnoverObservation
from src.repositories.liquidity_repository import LiquidityRepository

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

    TWSE_OPENAPI_URL = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
    TPEX_OPENAPI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index"

    @staticmethod
    def _iso_date(value: str) -> str:
        text = str(value).strip().replace(".", "/")
        parts = text.split("/")
        if len(parts) != 3:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        year = int(parts[0])
        if year < 1911:
            year += 1911
        return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"

    @classmethod
    def parse_twse_openapi(cls, payload: list[dict], trade_date: str) -> float | None:
        for row in payload:
            if cls._iso_date(row.get("Date", "")) == trade_date:
                return float(str(row["TradeValue"]).replace(",", ""))
        return None

    @classmethod
    def parse_tpex_openapi(cls, payload: list[dict], trade_date: str) -> float | None:
        for row in payload:
            if cls._iso_date(row.get("Date", "")) == trade_date:
                return float(str(row["TradeAmount"]).replace(",", ""))
        return None

    @staticmethod
    def _payload_hash(payload: object) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def import_official_turnover(
        self,
        trade_date: str,
        twse_payload: list[dict],
        tpex_payload: list[dict],
        available_at: str,
        fetched_at: str,
        revision: int = 1,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        twse = self.parse_twse_openapi(twse_payload, trade_date)
        tpex = self.parse_tpex_openapi(tpex_payload, trade_date)
        observation = MarketTurnoverObservation(
            trade_date=trade_date,
            twse_turnover_twd=twse,
            tpex_turnover_twd=tpex,
            twse_source="TWSE" if twse is not None else None,
            tpex_source="TPEx" if tpex is not None else None,
            twse_dataset="exchangeReport/FMTQIK" if twse is not None else None,
            tpex_dataset="tpex_daily_trading_index" if tpex is not None else None,
            twse_payload_hash=self._payload_hash(twse_payload),
            tpex_payload_hash=self._payload_hash(tpex_payload),
            available_at=available_at,
            fetched_at=fetched_at,
            revision=revision,
        )
        return LiquidityRepository(self.db_path).add_turnover(
            observation, ingested_at=ingested_at
        )

    def fetch_official_turnover(self, trade_date: str, fetched_at: str) -> dict[str, Any]:
        twse_response = requests.get(self.TWSE_OPENAPI_URL, timeout=15)
        tpex_response = requests.get(self.TPEX_OPENAPI_URL, timeout=15)
        twse_response.raise_for_status()
        tpex_response.raise_for_status()
        # Current OpenAPI retrieval proves availability no earlier than fetch time.
        return self.import_official_turnover(
            trade_date,
            twse_response.json(),
            tpex_response.json(),
            available_at=fetched_at,
            fetched_at=fetched_at,
        )

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
