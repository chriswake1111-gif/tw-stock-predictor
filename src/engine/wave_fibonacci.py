import os
import yaml
import logging
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class WaveFibonacciEngine:
    """杜金龍波浪理論與費波南希時間/空間演算引擎"""

    DEFAULT_FIB_NUMBERS = [8, 13, 21, 34, 55, 89, 144, 233]
    DEFAULT_FIB_RATIOS = [0.382, 0.5, 0.618, 1.0, 1.382, 1.618, 2.0, 2.618, 3.236]

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """從 YAML 配置檔讀取波浪與費氏參數"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"載入 {self.config_path} 失敗: {str(e)}")
        return {}

    def get_symbol_wave_params(self, symbol: str, df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        讀取指定標的在 config 中的波浪錨定點位 (P0, P1, P2, pivot_date)。
        若 config 中未特別設定，且提供了 df，則由歷史 K 線自動錨定歷史最低點 (P0) 與次高點 (P1)。
        """
        wave_params = self.config.get("wave_parameters", {})
        
        # 符號比對
        clean_symbol = symbol.replace(".TW", "").replace(".TWO", "")
        for key in [symbol, clean_symbol, f"{clean_symbol}.TW"]:
            if key in wave_params:
                return wave_params[key]
        
        # 特殊別名
        if symbol in ["0000", "TAIEX"] and "^TWII" in wave_params:
            return wave_params["^TWII"]

        # 自動錨定機制 (當使用者查詢任意股票且無硬編碼設定時)
        if df is not None and not df.empty and 'close' in df.columns:
            try:
                min_idx = df['close'].idxmin()
                p0 = float(df.loc[min_idx, 'close'])
                pivot_date = str(df.loc[min_idx, 'date'])
                
                # P0 之後的歷史最高價作為 P1
                after_p0_df = df.loc[min_idx:]
                if not after_p0_df.empty:
                    p1 = float(after_p0_df['close'].max())
                else:
                    p1 = float(df['close'].max())
                
                p2 = round(p1 - (p1 - p0) * 0.382, 2)
                return {"p0": p0, "p1": p1, "p2": p2, "pivot_date": pivot_date}
            except Exception as e:
                logger.error(f"自動推導 {symbol} 波浪點位失敗: {str(e)}")

        # 預設極值錨定
        return {"p0": 12629.0, "p1": 15475.0, "p2": 14001.0, "pivot_date": "2022-10-25"}

    def calculate_wave_targets(self, p0: float, p1: float, p2: Optional[float] = None) -> Dict[str, float]:
        """
        價格空間滿足點推導：
        給定浪 1 起點 P0 與浪 1 高點 P1，推導第 3 浪主升段與第 5 浪目標價
        :param p0: 浪 1 起點 (波段最低價)
        :param p1: 浪 1 頂點 (浪 1 高點)
        :param p2: 浪 2 底點 (若未提供，以黃金拉回 0.618 計算)
        """
        wave1_diff = p1 - p0
        if p2 is None:
            p2 = p1 - wave1_diff * 0.618

        targets = {
            "p0": p0,
            "p1": p1,
            "p2": p2,
            "wave1_amplitude": round(wave1_diff, 2),
            # 浪 3 主升段目標價 = P2 + wave1_amplitude * 黃金分割比率
            "wave3_1.382": round(p2 + wave1_diff * 1.382, 2),
            "wave3_1.618": round(p2 + wave1_diff * 1.618, 2),
            "wave3_2.000": round(p2 + wave1_diff * 2.000, 2),
            "wave3_2.618": round(p2 + wave1_diff * 2.618, 2),
            # 浪 5 末升段目標價 = P0 + wave1_amplitude * 3.236
            "wave5_1.000": round(p2 + wave1_diff * 1.618 + wave1_diff * 1.0, 2),
            "wave5_3.236": round(p0 + wave1_diff * 3.236, 2)
        }
        return targets

    def check_time_window(self, df: pd.DataFrame, pivot_date: str, is_monthly: bool = False) -> Dict[str, Any]:
        """
        費波南希時間轉折視窗判定：
        :param df: K 線 DataFrame (需包含 'date' 欄位，格式 'YYYY-MM-DD')
        :param pivot_date: 歷史關鍵轉折點日期
        :param is_monthly: True 代表月 K 線 (按自然月計算)；False 代表日 K 線 (按交易日數計算)
        """
        if df.empty or 'date' not in df.columns:
            return {"is_in_window": False, "reason": "DataFrame 為空或無 date 欄位"}

        fib_numbers = self.config.get("fibonacci", {}).get("numbers", self.DEFAULT_FIB_NUMBERS)
        df_sorted = df.sort_values("date").reset_index(drop=True)
        latest_date_str = df_sorted["date"].iloc[-1]
        
        if is_monthly:
            # 1. 月 K 線：按自然月份計算月數差
            p_dt = datetime.strptime(pivot_date, "%Y-%m-%d")
            l_dt = datetime.strptime(latest_date_str, "%Y-%m-%d")
            elapsed_units = (l_dt.year - p_dt.year) * 12 + (l_dt.month - p_dt.month)
            unit_name = "月"
        else:
            # 2. 日 K 線：按交易日數 (Row Count) 計算
            sub_df = df_sorted[df_sorted["date"] >= pivot_date]
            elapsed_units = len(sub_df)
            unit_name = "個交易日"

        # 比對是否進入費氏數字 +/- 1 轉折警戒視窗
        matching_fib = None
        is_in_window = False
        for fib in fib_numbers:
            if abs(elapsed_units - fib) <= 1:
                is_in_window = True
                matching_fib = fib
                break

        return {
            "pivot_date": pivot_date,
            "latest_date": latest_date_str,
            "is_monthly": is_monthly,
            "elapsed_units": elapsed_units,
            "unit_name": unit_name,
            "is_in_window": is_in_window,
            "matching_fib": matching_fib,
            "status_message": f"自基準日 {pivot_date} 起已歷經 {elapsed_units} {unit_name}" + 
                             (f" [觸發 費氏 {matching_fib} {unit_name} 時間轉折視窗警戒!]" if is_in_window else " [正常趨勢發展中]")
        }
