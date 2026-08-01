import os
import yaml
import sqlite3
import logging
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class FinMindCollector:
    """
    FinMind 台股財報 EPS (單季標準化轉換與真 TTM EPS 加總)、本益比/股價淨值比估值數據採集器 (Local-First SQLite 快取)
    """

    def __init__(self, db_path: str = "data/cache.db", api_token: Optional[str] = None):
        self.db_path = db_path
        self.api_token = api_token or os.getenv("FINMIND_API_TOKEN", "")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化估值與單季標準化 EPS SQLite 資料表與自動 Migration"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_valuation (
                    stock_id TEXT,
                    date TEXT,
                    pe REAL,
                    pb REAL,
                    yield_rate REAL,
                    yield_unit TEXT,
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
                    single_quarter_eps REAL,
                    year INTEGER,
                    quarter INTEGER,
                    PRIMARY KEY (stock_id, period_end)
                )
            """)
            # Migration 檢查：確保舊版 stock_quarterly_eps 具有新欄位
            cursor.execute("PRAGMA table_info(stock_valuation)")
            valuation_cols = [col[1] for col in cursor.fetchall()]
            if "symbol" in valuation_cols and "stock_id" not in valuation_cols:
                cursor.execute("ALTER TABLE stock_valuation RENAME COLUMN symbol TO stock_id")
                cursor.execute("PRAGMA table_info(stock_valuation)")
                valuation_cols = [col[1] for col in cursor.fetchall()]
            if "yield_unit" not in valuation_cols:
                cursor.execute("ALTER TABLE stock_valuation ADD COLUMN yield_unit TEXT")
                cursor.execute(
                    "UPDATE stock_valuation SET yield_unit='percent' WHERE yield_unit IS NULL"
                )

            cursor.execute("PRAGMA table_info(stock_quarterly_eps)")
            cols = [col[1] for col in cursor.fetchall()]
            if "quarterly_eps" in cols and "single_quarter_eps" not in cols:
                cursor.execute("ALTER TABLE stock_quarterly_eps RENAME COLUMN quarterly_eps TO single_quarter_eps")
            if "year" not in cols:
                cursor.execute("ALTER TABLE stock_quarterly_eps ADD COLUMN year INTEGER")
            if "quarter" not in cols:
                cursor.execute("ALTER TABLE stock_quarterly_eps ADD COLUMN quarter INTEGER")
            conn.commit()

    @staticmethod
    def _yield_to_ratio(value: Any, unit: str = "percent") -> Optional[float]:
        """依明確單位將殖利率統一為比例格式。"""
        if value is None or pd.isna(value):
            return None
        numeric = float(value)
        if unit == "ratio":
            return numeric
        if unit == "percent":
            return numeric / 100.0
        raise ValueError(f"Unsupported dividend-yield unit: {unit}")

    def normalize_quarterly_eps(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """
        財報單季 EPS 標準化演算：
        1. 過濾 EPS 項目。
        2. 若為累計值，依公式轉換為單季值：
           - Q1_single = Q1_cum
           - Q2_single = Q2_cum - Q1_cum
           - Q3_single = Q3_cum - Q2_cum
           - Q4_single = Full_Year - Q3_cum (或採用已公告 Q4 單季值)
        3. 同一財報區間取最新 origin_date/date 更正版本。
        """
        if raw_df.empty:
            return pd.DataFrame()

        # 過濾 EPS 相關項目
        eps_df = raw_df[raw_df["type"].str.upper().str.contains("EPS|EARNINGSPERSHARE", na=False)].copy()
        if eps_df.empty:
            return pd.DataFrame()

        # 解析年份與月份以對應 Q1~Q4
        eps_df["date"] = pd.to_datetime(eps_df["date"])
        eps_df["year"] = eps_df["date"].dt.year
        eps_df["month"] = eps_df["date"].dt.month
        eps_df["origin_date"] = pd.to_datetime(eps_df.get("origin_date", eps_df["date"]))

        # 排序：同一期間依 origin_date 升冪，確保保留最新更正版
        eps_df.sort_values(by=["date", "origin_date"], ascending=[True, True], inplace=True)

        records = {}
        for _, row in eps_df.iterrows():
            yr = int(row["year"])
            m = int(row["month"])
            val = float(row["value"])
            dt_str = row["date"].strftime("%Y-%m-%d")
            av_at = row["origin_date"].strftime("%Y-%m-%d")

            quarter_by_month = {3: 1, 6: 2, 9: 3, 12: 4}
            q = quarter_by_month.get(m)
            if q is None:
                logger.warning(f"忽略非標準季末 EPS 紀錄: {dt_str}")
                continue
            records[(yr, q)] = {
                "period_end": dt_str,
                "available_at": av_at,
                "raw_val": val,
                "year": yr,
                "quarter": q
            }

        # 轉為單季 EPS
        normalized = []
        years = sorted(list(set(k[0] for k in records.keys())))
        
        for yr in years:
            q1 = records.get((yr, 1))
            q2 = records.get((yr, 2))
            q3 = records.get((yr, 3))
            q4 = records.get((yr, 4))

            if q1:
                normalized.append({
                    "period_end": q1["period_end"],
                    "available_at": q1["available_at"],
                    "single_quarter_eps": round(q1["raw_val"], 2),
                    "year": yr,
                    "quarter": 1
                })

            if q2:
                q2_single = q2["raw_val"] - q1["raw_val"] if q1 else q2["raw_val"]
                normalized.append({
                    "period_end": q2["period_end"],
                    "available_at": q2["available_at"],
                    "single_quarter_eps": round(q2_single, 2),
                    "year": yr,
                    "quarter": 2
                })

            if q3:
                q3_single = q3["raw_val"] - q2["raw_val"] if q2 else (q3["raw_val"] - q1["raw_val"] if q1 else q3["raw_val"])
                normalized.append({
                    "period_end": q3["period_end"],
                    "available_at": q3["available_at"],
                    "single_quarter_eps": round(q3_single, 2),
                    "year": yr,
                    "quarter": 3
                })

            if q4:
                q4_single = q4["raw_val"] - q3["raw_val"] if q3 else (q4["raw_val"] - q2["raw_val"] if q2 else (q4["raw_val"] - q1["raw_val"] if q1 else q4["raw_val"]))
                normalized.append({
                    "period_end": q4["period_end"],
                    "available_at": q4["available_at"],
                    "single_quarter_eps": round(q4_single, 2),
                    "year": yr,
                    "quarter": 4
                })

        res_df = pd.DataFrame(normalized)
        if not res_df.empty:
            res_df.sort_values(by="period_end", inplace=True)
        return res_df

    def get_quarterly_eps(self, stock_id: str, as_of_date: Optional[str] = None) -> pd.DataFrame:
        """從 SQLite 快取或 FinMind API 獲取單季標準化 EPS"""
        clean_id = stock_id.replace(".TW", "").replace(".TWO", "").replace("^", "")
        cached_df = pd.DataFrame()
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                query = "SELECT stock_id, period_end, available_at, single_quarter_eps, year, quarter FROM stock_quarterly_eps WHERE stock_id=? ORDER BY period_end ASC"
                cached_df = pd.read_sql_query(query, conn, params=(clean_id,))

            eligible_cache = cached_df
            if as_of_date and not eligible_cache.empty:
                available_at = pd.to_datetime(eligible_cache["available_at"], errors="coerce")
                eligible_cache = eligible_cache[available_at <= pd.Timestamp(as_of_date)]

            if len(eligible_cache) >= 4:
                reference_date = pd.Timestamp(as_of_date or datetime.now().strftime("%Y-%m-%d"))
                latest_available = pd.to_datetime(eligible_cache["available_at"], errors="coerce").max()
                if pd.notna(latest_available) and (reference_date - latest_available).days <= 150:
                    return cached_df
        except Exception:
            cached_df = pd.DataFrame()

        try:
            url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={clean_id}&start_date=2020-01-01"
            if self.api_token:
                url += f"&token={self.api_token}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                res = resp.json()
                if res.get("msg") == "success" and "data" in res and res["data"]:
                    raw_df = pd.DataFrame(res["data"])
                    norm_df = self.normalize_quarterly_eps(raw_df)
                    if not norm_df.empty:
                        with sqlite3.connect(self.db_path) as conn:
                            cursor = conn.cursor()
                            for _, r in norm_df.iterrows():
                                cursor.execute("""
                                    INSERT OR REPLACE INTO stock_quarterly_eps (stock_id, period_end, available_at, single_quarter_eps, year, quarter)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                """, (clean_id, str(r["period_end"]), str(r["available_at"]), float(r["single_quarter_eps"]), int(r["year"]), int(r["quarter"])))
                            conn.commit()
                        norm_df["stock_id"] = clean_id
                        return norm_df
        except Exception as e:
            logger.error(f"FinMind EPS 採集與標準化失敗: {str(e)}")

        return cached_df

    def get_ttm_eps(self, stock_id: str, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        計算真實連續 4 季單季 EPS 加總之 TTM EPS。
        若不滿 4 季連續單季紀錄，回傳 status: "insufficient_data"，絕不安裝代理假值。
        """
        clean_id = stock_id.replace(".TW", "").replace(".TWO", "").replace("^", "")
        eps_df = self.get_quarterly_eps(clean_id, as_of_date=as_of_date)

        if eps_df.empty:
            return {
                "status": "insufficient_data",
                "reason": f"No normalized quarterly EPS records available for {clean_id}",
                "eps": None
            }

        if as_of_date:
            available_at = pd.to_datetime(eps_df["available_at"], errors="coerce")
            eps_df = eps_df[available_at <= pd.Timestamp(as_of_date)]

        if len(eps_df) < 4:
            return {
                "status": "insufficient_data",
                "reason": f"Fewer than four normalized quarterly EPS records available (found {len(eps_df)})",
                "eps": None
            }

        eps_df = eps_df.copy()
        eps_df["year"] = pd.to_numeric(eps_df["year"], errors="coerce")
        eps_df["quarter"] = pd.to_numeric(eps_df["quarter"], errors="coerce")
        eps_df.dropna(subset=["year", "quarter", "single_quarter_eps"], inplace=True)
        eps_df.sort_values(["year", "quarter", "available_at"], inplace=True)
        eps_df.drop_duplicates(["year", "quarter"], keep="last", inplace=True)

        latest_four = eps_df.iloc[-4:]
        quarter_ordinals = (
            latest_four["year"].astype(int) * 4 + latest_four["quarter"].astype(int)
        ).tolist()
        if len(latest_four) < 4 or any(
            current - previous != 1
            for previous, current in zip(quarter_ordinals, quarter_ordinals[1:])
        ):
            return {
                "status": "insufficient_data",
                "reason": "The latest normalized EPS records are not four consecutive quarters",
                "eps": None
            }

        ttm_val = round(float(latest_four["single_quarter_eps"].sum()), 2)
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

        try:
            with sqlite3.connect(self.db_path) as conn:
                query = "SELECT stock_id, date, pe, pb, yield_rate, yield_unit FROM stock_valuation WHERE stock_id=? AND date>=? AND date<=? ORDER BY date ASC"
                df = pd.read_sql_query(query, conn, params=(clean_id, start_date, end_date))
                if not df.empty and len(df) >= 10:
                    df["yield_rate"] = df.apply(
                        lambda row: self._yield_to_ratio(
                            row["yield_rate"], row.get("yield_unit") or "percent"
                        ),
                        axis=1
                    )
                    return df
        except Exception:
            pass

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
                            yield_rate = self._yield_to_ratio(r.get("yield_rate"), "percent")
                            cursor.execute("""
                                INSERT OR REPLACE INTO stock_valuation (stock_id, date, pe, pb, yield_rate, yield_unit)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (clean_id, str(r["date"]), float(r.get("pe", 0)), float(r.get("pb", 0)), yield_rate, "ratio"))
                            records.append({"stock_id": clean_id, "date": str(r["date"]), "pe": float(r.get("pe", 0)), "pb": float(r.get("pb", 0)), "yield_rate": yield_rate})
                        conn.commit()
                    return pd.DataFrame(records)
        except Exception as e:
            logger.error(f"FinMind PE/PB 採集失敗: {str(e)}")

        return pd.DataFrame()
