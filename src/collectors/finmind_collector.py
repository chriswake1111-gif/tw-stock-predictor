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

class FinMindCollector:
    """FinMind 台股財報 EPS、本益比/股價淨值比估值數據與融資融券籌碼採集器 (Local-First SQLite 快取)"""

    def __init__(self, db_path: str = "data/cache.db", api_token: Optional[str] = None):
        self.db_path = db_path
        self.api_token = api_token or os.getenv("FINMIND_API_TOKEN", "")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化估值與籌碼 SQLite 資料表"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_valuation (
                    stock_id TEXT,
                    date TEXT,
                    pe REAL,
                    pb REAL,
                    yield_rate REAL,
                    PRIMARY KEY (stock_id, date)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_margin (
                    stock_id TEXT,
                    date TEXT,
                    margin_buy REAL,
                    margin_sell REAL,
                    margin_balance REAL,
                    short_buy REAL,
                    short_sell REAL,
                    short_balance REAL,
                    PRIMARY KEY (stock_id, date)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_quarterly_eps (
                    stock_id TEXT,
                    period_end TEXT,
                    available_at TEXT,
                    quarterly_eps REAL,
                    PRIMARY KEY (stock_id, period_end)
                )
            """)
            conn.commit()

    def get_quarterly_eps(self, stock_id: str) -> pd.DataFrame:
        """從 SQLite 快取或 FinMind API 獲取單季 EPS"""
        clean_id = stock_id.replace(".TW", "").replace(".TWO", "").replace("^", "")
        
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT stock_id, period_end, available_at, quarterly_eps FROM stock_quarterly_eps WHERE stock_id=? ORDER BY period_end ASC"
            df = pd.read_sql_query(query, conn, params=(clean_id,))
            if not df.empty:
                return df

        # 從 FinMind API 獲取財報 EPS 數據 (TaiwanStockFinancialStatements)
        try:
            url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={clean_id}&start_date=2020-01-01"
            if self.api_token:
                url += f"&token={self.api_token}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                res = resp.json()
                if res.get("msg") == "success" and "data" in res and res["data"]:
                    raw_df = pd.DataFrame(res["data"])
                    # 過濾 EPS (EarningsPerShare) 欄位
                    eps_df = raw_df[raw_df["type"] == "EPS"].copy()
                    if not eps_df.empty:
                        records = []
                        with sqlite3.connect(self.db_path) as conn:
                            cursor = conn.cursor()
                            for _, r in eps_df.iterrows():
                                period_end = str(r["date"])
                                av_at = str(r.get("origin_date", r["date"]))
                                eps_val = float(r["value"])
                                cursor.execute("""
                                    INSERT OR REPLACE INTO stock_quarterly_eps (stock_id, period_end, available_at, quarterly_eps)
                                    VALUES (?, ?, ?, ?)
                                """, (clean_id, period_end, av_at, eps_val))
                                records.append({"stock_id": clean_id, "period_end": period_end, "available_at": av_at, "quarterly_eps": eps_val})
                            conn.commit()
                        return pd.DataFrame(records)
        except Exception as e:
            logger.error(f"FinMind EPS 採集失敗: {str(e)}")

        return pd.DataFrame()

    def get_ttm_eps(self, stock_id: str, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        計算真正已公告四季單季 EPS 累計之 TTM EPS (sum_latest_four_reported_quarter_eps)
        若不足 4 季則回傳 status: "insufficient_data"，絕不偽裝為 historical_ttm。
        """
        clean_id = stock_id.replace(".TW", "").replace(".TWO", "").replace("^", "")
        eps_df = self.get_quarterly_eps(clean_id)

        if eps_df.empty:
            return {
                "status": "insufficient_data",
                "reason": f"No quarterly EPS financial records available for {clean_id}",
                "eps": None
            }

        if as_of_date:
            eps_df = eps_df[eps_df["available_at"] <= as_of_date]

        if len(eps_df) < 4:
            return {
                "status": "insufficient_data",
                "reason": f"Fewer than four normalized quarterly EPS records available (found {len(eps_df)})",
                "eps": None
            }

        latest_four = eps_df.iloc[-4:]
        ttm_val = round(float(latest_four["quarterly_eps"].sum()), 2)
        latest_row = latest_four.iloc[-1]

        return {
            "status": "available",
            "eps": {
                "value": ttm_val,
                "type": "historical_ttm",
                "period_end": str(latest_row["period_end"]),
                "available_at": str(latest_row["available_at"]),
                "unit": "TWD_per_share",
                "source": "FinMind",
                "dataset": "TaiwanStockFinancialStatements",
                "calculation": "sum_latest_four_reported_quarter_eps",
                "status": "available"
            }
        }

    def get_valuation(self, stock_id: str, start_date: str = "2020-01-01", end_date: Optional[str] = None) -> pd.DataFrame:
        clean_id = stock_id.replace(".TW", "").replace(".TWO", "").replace("^", "")
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT stock_id, date, pe, pb, yield_rate FROM stock_valuation WHERE stock_id=? AND date>=? AND date<=? ORDER BY date ASC"
            df = pd.read_sql_query(query, conn, params=(clean_id, start_date, end_date))
            if not df.empty and len(df) >= 10:
                return df

        try:
            url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPER&data_id={clean_id}&start_date={start_date}&end_date={end_date}"
            if self.api_token:
                url += f"&token={self.api_token}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                res = resp.json()
                if res.get("msg") == "success" and "data" in res and res["data"]:
                    raw_df = pd.DataFrame(res["data"])
                    raw_df.rename(columns={"PER": "pe", "PBR": "pb", "dividend_yield": "yield_rate"}, inplace=True)
                    records = []
                    with sqlite3.connect(self.db_path) as conn:
                        cursor = conn.cursor()
                        for _, r in raw_df.iterrows():
                            cursor.execute("""
                                INSERT OR REPLACE INTO stock_valuation (stock_id, date, pe, pb, yield_rate)
                                VALUES (?, ?, ?, ?, ?)
                            """, (clean_id, str(r["date"]), float(r.get("pe", 0)), float(r.get("pb", 0)), float(r.get("yield_rate", 0))))
                            records.append({"stock_id": clean_id, "date": str(r["date"]), "pe": float(r.get("pe", 0)), "pb": float(r.get("pb", 0)), "yield_rate": float(r.get("yield_rate", 0))})
                        conn.commit()
                    return pd.DataFrame(records)
        except Exception as e:
            logger.error(f"FinMind PE/PB 採集失敗: {str(e)}")

        return pd.DataFrame()

    def get_margin_trading(self, stock_id: str, start_date: str = "2020-01-01", end_date: Optional[str] = None) -> pd.DataFrame:
        clean_id = stock_id.replace(".TW", "").replace(".TWO", "").replace("^", "")
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT stock_id, date, margin_buy, margin_sell, margin_balance, short_buy, short_sell, short_balance FROM stock_margin WHERE stock_id=? AND date>=? AND date<=? ORDER BY date ASC"
            df = pd.read_sql_query(query, conn, params=(clean_id, start_date, end_date))
            if not df.empty and len(df) >= 10:
                return df

        return pd.DataFrame()
