import os
import yaml
import sqlite3
import logging
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class CBCCollector:
    """中央銀行 M1B 貨幣供給數據採集器 (零假值、零預設備援)"""

    def __init__(self, db_path: str = "data/cache.db", config_path: str = "config/config.yaml"):
        self.db_path = db_path
        self.config_path = config_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化 M1B SQLite 資料表，包含 period 與 available_at 欄位 migration"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cbc_m1b (
                    date TEXT PRIMARY KEY,
                    m1b_amount REAL,
                    period TEXT,
                    available_at TEXT
                )
            """)
            cursor.execute("PRAGMA table_info(cbc_m1b)")
            cols = [col[1] for col in cursor.fetchall()]
            if "period" not in cols:
                cursor.execute("ALTER TABLE cbc_m1b ADD COLUMN period TEXT")
            if "available_at" not in cols:
                cursor.execute("ALTER TABLE cbc_m1b ADD COLUMN available_at TEXT")
            conn.commit()

    def get_latest_m1b(self, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        獲取最新可用 M1B 貨幣供給量 Contract。
        無真實數據時一律回傳 status: "insufficient_data"，絕不安裝假資料或硬編碼預設值。
        """
        try:
            cutoff = str(as_of_date or datetime.now().strftime("%Y-%m-%d"))
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT date, m1b_amount, period, available_at
                    FROM cbc_m1b
                    WHERE available_at IS NOT NULL AND available_at <= ?
                    ORDER BY available_at DESC, period DESC
                    LIMIT 1
                    """,
                    (cutoff,)
                )
                row = cursor.fetchone()

            if row and row[1]:
                date_val, amount_val, period_val, av_at_val = row
                return {
                    "status": "available",
                    "value": float(amount_val),
                    "period": period_val or date_val[:7],
                    "available_at": av_at_val,
                    "unit": "TWD_100_million",
                    "source": "CBC"
                }
        except Exception as e:
            logger.error(f"讀取 SQLite M1B 數據失敗: {str(e)}")

        return {
            "status": "insufficient_data",
            "value": None,
            "reason": "No CBC M1B record is available in local cache or remote source"
        }

    def save_m1b_data(self, date_str: str, m1b_amount: float, period_str: Optional[str] = None, available_at_str: Optional[str] = None):
        """手動或爬蟲更新 M1B 數據至 SQLite"""
        period_val = period_str or date_str[:7]
        av_at_val = available_at_str or datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO cbc_m1b (date, m1b_amount, period, available_at)
                VALUES (?, ?, ?, ?)
            """, (date_str, m1b_amount, period_val, av_at_val))
            conn.commit()
        logger.info(f"已更新 SQLite M1B 數據 ({date_str}: {m1b_amount} 億元, period: {period_val})")
