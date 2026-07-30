import os
import yaml
import sqlite3
import logging
import requests
import pandas as pd
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class CBCCollector:
    """中央銀行 M1B 貨幣供給數據採集器，支援 Local-First SQLite 與預設基準值降級備援機制。"""

    def __init__(self, db_path: str = "data/cache.db", config_path: str = "config/config.yaml"):
        self.db_path = db_path
        self.config_path = config_path
        self.default_m1b = self._load_default_m1b()
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _load_default_m1b(self) -> float:
        """從 YAML 設定檔載入預設 M1B 基準值 (單位: 億新台幣)"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                    return cfg.get("sentiment", {}).get("default_m1b_billion", 270000.0)
            except Exception as e:
                logger.error(f"讀取 {self.config_path} 失敗: {str(e)}")
        return 270000.0

    def _init_db(self):
        """初始化 M1B SQLite 資料表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cbc_m1b (
                date TEXT PRIMARY KEY,
                m1b_amount REAL
            )
        """)
        conn.commit()
        conn.close()

    def get_latest_m1b(self) -> float:
        """
        獲取最新可用 M1B 貨幣供給量 (單位: 億新台幣)
        優先尋找本地 SQLite 最新的快取；若無數據則回退至 config 預設基準值。
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT m1b_amount FROM cbc_m1b ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            logger.info(f"從 SQLite 快取獲取最新 M1B 數據: {row[0]} 億元")
            return float(row[0])

        logger.info(f"使用 config.yaml 備用 M1B 基準值: {self.default_m1b} 億元")
        return self.default_m1b

    def save_m1b_data(self, date_str: str, m1b_amount: float):
        """手動或爬蟲更新 M1B 數據至 SQLite"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO cbc_m1b (date, m1b_amount)
            VALUES (?, ?)
        """, (date_str, m1b_amount))
        conn.commit()
        conn.close()
        logger.info(f"已更新 SQLite M1B 數據 ({date_str}: {m1b_amount} 億元)")
