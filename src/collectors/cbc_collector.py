import os
import yaml
import sqlite3
import logging
import requests
import pandas as pd
import calendar
import hashlib
import json
from datetime import datetime
from typing import Dict, Any, Optional

from src.domain.liquidity import M1BMonthlyObservation
from src.repositories.liquidity_repository import LiquidityRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class CBCCollector:
    """中央銀行 M1B 貨幣供給數據採集器 (零假值、零預設備援)"""

    def __init__(self, db_path: str = "data/cache.db", config_path: str = "config/config.yaml"):
        self.db_path = db_path
        self.config_path = config_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    OFFICIAL_M1B_URL = "https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=EF15M01"
    OFFICIAL_M1B_DATASET = "CBC EF15M01"

    @staticmethod
    def _cbc_period(period: str) -> tuple[str, str]:
        text = str(period).strip()
        if "M" not in text:
            raise ValueError(f"unsupported CBC monthly period: {period}")
        year_text, month_text = text.split("M", 1)
        year, month = int(year_text), int(month_text)
        last_day = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}", f"{year:04d}-{month:02d}-{last_day:02d}"

    @classmethod
    def parse_official_m1b(
        cls,
        payload: dict,
        available_at_by_period: dict[str, str],
        fetched_at: str,
    ) -> dict[str, Any]:
        """Parse EF15M01 without inventing historical publication timestamps."""
        official_data = payload.get("data", {})
        if isinstance(official_data, dict) and "dataSets" in official_data:
            structure = official_data.get("structure", {})
            data = official_data.get("dataSets", [])
            categories = [item.get("data", "") for item in structure.get("Table1", [])]
        else:  # compact deterministic fixture format
            result = payload.get("result", {})
            tables = result.get("structure", {}).get("tables", [])
            data = result.get("data", [])
            categories = next(
                (table.get("items", []) for table in tables if table.get("name") == "Table1"),
                [],
            )
        m1b_index = next(
            (
                index for index, item in enumerate(categories)
                if "M1B" in str(item).upper().replace(" ", "")
                or "Ｍ１Ｂ" in str(item).replace(" ", "")
            ),
            None,
        )
        if m1b_index is None:
            raise ValueError("CBC EF15M01 M1B category was not found")
        value_index = 1 + m1b_index * 2
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        observations: list[M1BMonthlyObservation] = []
        missing_periods: list[str] = []
        for row in data:
            period, data_date = cls._cbc_period(row[0])
            available_at = available_at_by_period.get(period)
            if not available_at:
                missing_periods.append(period)
                continue
            observations.append(M1BMonthlyObservation(
                period=period,
                value_raw=float(str(row[value_index]).replace(",", "")),
                raw_unit="TWD_million",
                data_date=data_date,
                available_at=available_at,
                fetched_at=fetched_at,
                source="CBC",
                source_dataset=cls.OFFICIAL_M1B_DATASET,
                source_url=cls.OFFICIAL_M1B_URL,
                payload_hash=payload_hash,
            ))
        return {
            "status": (
                "partial" if observations and missing_periods
                else "available" if observations
                else "needs_human_input"
            ),
            "observations": observations,
            "missing_available_at_periods": missing_periods,
            "reason": None if observations else "official_release_timestamp_required",
        }

    def import_official_m1b(
        self,
        payload: dict,
        available_at_by_period: dict[str, str],
        fetched_at: str,
        ingested_at: str | None = None,
    ) -> dict[str, Any]:
        parsed = self.parse_official_m1b(payload, available_at_by_period, fetched_at)
        repository = LiquidityRepository(self.db_path)
        parsed["records"] = [
            repository.add_m1b(observation, ingested_at=ingested_at)
            for observation in parsed["observations"]
        ]
        parsed.pop("observations")
        return parsed

    def fetch_official_m1b(
        self, available_at_by_period: dict[str, str], fetched_at: str
    ) -> dict[str, Any]:
        response = requests.get(self.OFFICIAL_M1B_URL, timeout=15)
        response.raise_for_status()
        return self.import_official_m1b(
            response.json(), available_at_by_period, fetched_at
        )

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
