import os
import sqlite3
import logging
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class TWSECollector:
    """證交所 / yfinance 數據採集器，支援 Local-First SQLite 快取機制。"""

    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化 SQLite 本地快取資料表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. 每日 OHLCV 快取表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_ohlcv (
                symbol TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (symbol, date)
            )
        """)
        
        # 2. 全市場 / 個股融資籌碼快取表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS margin_trading (
                date TEXT,
                symbol TEXT,
                margin_buy INTEGER,
                margin_sell INTEGER,
                margin_balance INTEGER,
                PRIMARY KEY (symbol, date)
            )
        """)
        
        conn.commit()
        conn.close()

    def get_ohlcv(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        獲取 K 線 OHLCV 數據 (Local-First: 先查本地 SQLite，不足再由 yfinance 補全)
        :param symbol: 股票代號 (如 '2330.TW' 或 '^TWII')
        :param start_date: 開始日期 ('YYYY-MM-DD')
        :param end_date: 結束日期 ('YYYY-MM-DD')
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        # 符號標準化 (如果是 '2330' 轉為 '2330.TW'，'0000' 轉為 '^TWII')
        yf_symbol = symbol
        if symbol == "0000" or symbol == "TAIEX":
            yf_symbol = "^TWII"
        elif not (symbol.startswith("^") or "." in symbol):
            yf_symbol = f"{symbol}.TW"

        # 1. 查詢 SQLite 本地資料
        conn = sqlite3.connect(self.db_path)
        query = """
            SELECT date, open, high, low, close, volume, symbol 
            FROM daily_ohlcv 
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
        """
        cached_df = pd.read_sql_query(query, conn, params=(yf_symbol, start_date, end_date))
        conn.close()

        # 簡易檢查：如果本地已有資料且筆數充足（簡單判斷近天數），直接回傳
        # 否則從 yfinance 抓取更新
        if not cached_df.empty and len(cached_df) >= (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days * 0.5:
            logger.info(f"從本地快取成功載入 {yf_symbol} 共 {len(cached_df)} 筆 K 線資料")
            return cached_df

        logger.info(f"從 yfinance 下載 {yf_symbol} 歷史 K 線數據 ({start_date} ~ {end_date})...")
        try:
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(start=start_date, end=end_date)
            
            if df.empty:
                logger.warning(f"yfinance 未能抓取到 {yf_symbol} 數據")
                return cached_df

            df = df.reset_index()
            # 轉換欄位名稱
            df = df.rename(columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume"
            })
            
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df["symbol"] = yf_symbol
            
            records = df[["symbol", "date", "open", "high", "low", "close", "volume"]].copy()
            
            # 寫入 SQLite 快取
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            for _, row in records.iterrows():
                cursor.execute("""
                    INSERT OR REPLACE INTO daily_ohlcv (symbol, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (row['symbol'], row['date'], row['open'], row['high'], row['low'], row['close'], row['volume']))
            conn.commit()
            conn.close()
            logger.info(f"已更新 SQLite 快取 {yf_symbol} 共 {len(records)} 筆 K 線資料")

            return records
        except Exception as e:
            logger.error(f"抓取 {yf_symbol} 數據失敗: {str(e)}")
            return cached_df

    def get_market_margin(self, date_str: str = None) -> dict:
        """
        從 TWSE 證交所官網抓取全市場融資餘額數據 (格式 YYYYMMDD)
        """
        if not date_str:
            date_str = datetime.now().strftime("%Y%m%d")
        else:
            date_str = date_str.replace("-", "")

        url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=MS&response=json"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("stat") == "OK" and "tables" in data:
                    # 解析全市場融資餘額
                    tables = data["tables"]
                    if tables and len(tables) > 0:
                        margin_table = tables[0]
                        # 回傳摘要
                        return {"status": "success", "date": date_str, "data": margin_table.get("data", [])}
            logger.warning(f"TWSE 融資餘額 API 回傳無數據或非交易日 (Date: {date_str})")
            return {"status": "empty", "date": date_str}
        except Exception as e:
            logger.error(f"連線 TWSE API 失敗: {str(e)}")
            return {"status": "error", "message": str(e)}
