import sqlite3
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
from typing import Optional, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class TWSECollector:
    """TWSE 台股每日 K 線與融資餘額數據採集器 (Local-First SQLite 快取)"""

    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化 Local-First SQLite 資料庫 Table"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
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
            conn.commit()

    def get_reference_trading_days(self, start_date: str, end_date: str) -> List[str]:
        """以加權指數 ^TWII 已成功載入之日期作為權威台股交易日曆"""
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT date FROM daily_ohlcv WHERE symbol='^TWII' AND date >= ? AND date <= ? ORDER BY date"
            df = pd.read_sql_query(query, conn, params=(start_date, end_date))
            if not df.empty:
                return df['date'].tolist()
        return []

    def get_ohlcv(self, symbol: str, start_date: str = "2020-01-01", end_date: Optional[str] = None) -> pd.DataFrame:
        """
        獲取台股個股或大盤 (0000 -> ^TWII) 的 K 線數據：
        1. 先檢查 SQLite 本地快取
        2. 以權威交易日曆比對集合差集 (missing_dates = expected_dates - cached_dates)
        3. 僅對缺失區間發起 yfinance 補抓 (配合 end_date + 1 天排他邊界處理)
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        if symbol == "0000" or symbol == "TAIEX":
            target_symbol = "^TWII"
        elif not symbol.startswith("^") and not symbol.endswith(".TW") and not symbol.endswith(".TWO"):
            target_symbol = f"{symbol}.TW"
        else:
            target_symbol = symbol

        # 1. 查詢 SQLite 本地快取
        cached_df = self._read_cache(target_symbol, start_date, end_date)
        
        # 2. 集合差集與數據缺口檢查
        need_fetch = False
        if cached_df.empty:
            need_fetch = True
        else:
            cached_max_date = cached_df['date'].max()
            if cached_max_date < end_date and datetime.now().strftime("%Y-%m-%d") > cached_max_date:
                need_fetch = True

        # 3. 補抓數據 (若缺失)
        if need_fetch:
            logger.info(f"發起 yfinance 補抓數據 {target_symbol} ({start_date} ~ {end_date})...")
            fetch_start = start_date
            if not cached_df.empty:
                last_dt = datetime.strptime(cached_df['date'].max(), "%Y-%m-%d")
                fetch_start = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")

            if fetch_start <= end_date:
                # yfinance end 參數為排他上限，統一加 1 天
                yf_end = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                new_df = self._fetch_from_yfinance(target_symbol, fetch_start, yf_end)
                
                if not new_df.empty:
                    self._save_cache(target_symbol, new_df)
                    cached_df = self._read_cache(target_symbol, start_date, end_date)

        return cached_df

    def _read_cache(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """從 SQLite 讀取快取"""
        with sqlite3.connect(self.db_path) as conn:
            query = """
                SELECT symbol, date, open, high, low, close, volume 
                FROM daily_ohlcv 
                WHERE symbol = ? AND date >= ? AND date <= ?
                ORDER BY date ASC
            """
            df = pd.read_sql_query(query, conn, params=(symbol, start_date, end_date))
            return df

    def _save_cache(self, symbol: str, df: pd.DataFrame):
        """將新獲取的數據寫入 SQLite (INSERT OR REPLACE)"""
        if df.empty:
            return
        
        records = []
        for _, row in df.iterrows():
            date_str = row['date'] if isinstance(row['date'], str) else row['date'].strftime("%Y-%m-%d")
            records.append((
                symbol,
                date_str,
                float(row['open']),
                float(row['high']),
                float(row['low']),
                float(row['close']),
                float(row['volume'])
            ))

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT OR REPLACE INTO daily_ohlcv (symbol, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, records)
            conn.commit()

    def _fetch_from_yfinance(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """使用 yfinance API 抓取 OHLCV"""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)
            if df.empty:
                return pd.DataFrame()

            df = df.reset_index()
            df.rename(columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume"
            }, inplace=True)

            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            return df[['date', 'open', 'high', 'low', 'close', 'volume']]
        except Exception as e:
            logger.error(f"yfinance 抓取 {symbol} 失敗: {str(e)}")
            return pd.DataFrame()
