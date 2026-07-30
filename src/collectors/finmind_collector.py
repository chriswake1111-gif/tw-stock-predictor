import os
import sqlite3
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class FinMindCollector:
    """FinMind API 數據採集器，獲取個股籌碼 (融資買賣超)、估值 (PE/PB/殖利率) 與 EPS 財報數據。"""

    def __init__(self, db_path: str = "data/cache.db", api_token: str = None):
        self.db_path = db_path
        self.api_token = api_token or os.getenv("FINMIND_API_TOKEN", "")
        self.base_url = "https://api.finmindtrade.com/api/v4/data"
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化估值與籌碼快取資料表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 估值資料表 (PE/PB/殖利率/EPS)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_valuation (
                symbol TEXT,
                date TEXT,
                pe REAL,
                pb REAL,
                yield_rate REAL,
                eps REAL,
                PRIMARY KEY (symbol, date)
            )
        """)
        
        # 個股融資籌碼表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_margin (
                symbol TEXT,
                date TEXT,
                margin_buy INTEGER,
                margin_sell INTEGER,
                margin_balance INTEGER,
                PRIMARY KEY (symbol, date)
            )
        """)
        
        conn.commit()
        conn.close()

    def _fetch_finmind(self, dataset: str, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """通用 FinMind REST API 請求函式"""
        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        if self.api_token:
            params["token"] = self.api_token

        try:
            resp = requests.get(self.base_url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("msg") == "success" and "data" in data:
                    df = pd.DataFrame(data["data"])
                    return df
            logger.warning(f"FinMind API API 回傳無數據: dataset={dataset}, stock_id={stock_id}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"FinMind API 連線失敗: {str(e)}")
            return pd.DataFrame()

    def get_valuation(self, stock_id: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        獲取本益比 (PE)、股價淨值比 (PB)、殖利率 (TaiwanStockPER)
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        # 1. 先查本地快取
        conn = sqlite3.connect(self.db_path)
        query = """
            SELECT symbol as stock_id, date, pe, pb, yield_rate 
            FROM stock_valuation 
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
        """
        cached_df = pd.read_sql_query(query, conn, params=(stock_id, start_date, end_date))
        conn.close()

        if not cached_df.empty and len(cached_df) >= 10:
            logger.info(f"從本地快取載入 {stock_id} 估值資料共 {len(cached_df)} 筆")
            return cached_df

        # 2. 本地沒有，呼叫 FinMind API
        logger.info(f"從 FinMind API 獲取 {stock_id} PE/PB/殖利率資料...")
        df = self._fetch_finmind("TaiwanStockPER", stock_id, start_date, end_date)
        if df.empty:
            return cached_df

        # 標準化欄位
        if "PER" in df.columns:
            df = df.rename(columns={"PER": "pe", "PBR": "pb", "DividendYield": "yield_rate"})
        
        # 寫入 SQLite 快取
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        for _, row in df.iterrows():
            pe_val = row.get("pe", 0.0)
            pb_val = row.get("pb", 0.0)
            yield_val = row.get("yield_rate", 0.0)
            date_val = row.get("date", "")
            cursor.execute("""
                INSERT OR REPLACE INTO stock_valuation (symbol, date, pe, pb, yield_rate)
                VALUES (?, ?, ?, ?, ?)
            """, (stock_id, date_val, pe_val, pb_val, yield_val))
        conn.commit()
        conn.close()

        return df

    def get_margin_trading(self, stock_id: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        獲取個股融資買賣超與融資餘額 (TaiwanStockMarginPurchaseShortSale)
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        df = self._fetch_finmind("TaiwanStockMarginPurchaseShortSale", stock_id, start_date, end_date)
        if df.empty:
            return pd.DataFrame()

        # 寫入 SQLite 快取
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        for _, row in df.iterrows():
            date_val = row.get("date", "")
            buy_val = row.get("MarginPurchaseBuy", 0)
            sell_val = row.get("MarginPurchaseSell", 0)
            bal_val = row.get("MarginPurchaseTodayBalance", 0)
            cursor.execute("""
                INSERT OR REPLACE INTO stock_margin (symbol, date, margin_buy, margin_sell, margin_balance)
                VALUES (?, ?, ?, ?, ?)
            """, (stock_id, date_val, buy_val, sell_val, bal_val))
        conn.commit()
        conn.close()

        return df

    def get_eps_financials(self, stock_id: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        獲取歷史財報 EPS (TaiwanStockFinancialStatements)
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        df = self._fetch_finmind("TaiwanStockFinancialStatements", stock_id, start_date, end_date)
        if not df.empty and "type" in df.columns:
            # 篩選 每股盈餘 (EPS) 項目
            eps_df = df[df["type"] == "EPS"].copy()
            return eps_df
        return df
