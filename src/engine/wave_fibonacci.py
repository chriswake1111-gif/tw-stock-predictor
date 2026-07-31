import os
import yaml
import logging
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List, Optional, Literal
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ConfirmedPivot:
    pivot_type: Literal["high", "low"]
    pivot_index: int
    pivot_date: str
    pivot_price: float
    confirmed_at_index: int
    confirmed_at: str

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

    def detect_confirmed_pivots(self, df: pd.DataFrame, confirmation_bars: int = 3, lookback: int = 5) -> List[ConfirmedPivot]:
        """
        無前視偏誤 (No-Repaint) 波浪轉折演算法：
        對候選低點 i，需滿足 past lookback 筆最低，且右側 confirmation_bars 筆之 Low 均高於 Low[i]。
        轉折點只在 confirmed_at (i + confirmation_bars) 時刻發布，完全符合 Prefix Invariance 原則。
        """
        if df.empty or len(df) < (lookback + confirmation_bars + 1):
            return []

        df_sorted = df.sort_values("date").reset_index(drop=True)
        pivots = []

        for i in range(lookback, len(df_sorted) - confirmation_bars):
            curr_low = df_sorted.loc[i, 'low']
            curr_high = df_sorted.loc[i, 'high']

            # 檢查是否為 Low Pivot
            past_lows = df_sorted.loc[i-lookback:i-1, 'low']
            future_lows = df_sorted.loc[i+1:i+confirmation_bars, 'low']

            if curr_low < past_lows.min() and (future_lows > curr_low).all():
                confirmed_idx = i + confirmation_bars
                pivots.append(ConfirmedPivot(
                    pivot_type="low",
                    pivot_index=i,
                    pivot_date=str(df_sorted.loc[i, 'date']),
                    pivot_price=float(curr_low),
                    confirmed_at_index=confirmed_idx,
                    confirmed_at=str(df_sorted.loc[confirmed_idx, 'date'])
                ))

            # 檢查是否為 High Pivot
            past_highs = df_sorted.loc[i-lookback:i-1, 'high']
            future_highs = df_sorted.loc[i+1:i+confirmation_bars, 'high']

            if curr_high > past_highs.max() and (future_highs < curr_high).all():
                confirmed_idx = i + confirmation_bars
                pivots.append(ConfirmedPivot(
                    pivot_type="high",
                    pivot_index=i,
                    pivot_date=str(df_sorted.loc[i, 'date']),
                    pivot_price=float(curr_high),
                    confirmed_at_index=confirmed_idx,
                    confirmed_at=str(df_sorted.loc[confirmed_idx, 'date'])
                ))

        return pivots

    def get_realtime_confirmed_wave_params(self, symbol: str, df: pd.DataFrame, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        即時模式：只使用 confirmed_at <= as_of_date 的已確認轉折點算號。
        嚴格驗證轉折時間順序：index(P0) < index(P1) 且 price(P0) < price(P1)。
        若不足時回傳 status: "insufficient_data"，絕不安裝預設備援極值。
        """
        if df.empty:
            return {"status": "insufficient_data", "reason": "K line dataframe is empty"}

        if as_of_date is None:
            as_of_date = str(df['date'].iloc[-1])

        pivots = self.detect_confirmed_pivots(df)
        valid_pivots = [p for p in pivots if p.confirmed_at <= as_of_date]

        low_pivots = [p for p in valid_pivots if p.pivot_type == "low"]
        high_pivots = [p for p in valid_pivots if p.pivot_type == "high"]

        if not low_pivots or not high_pivots:
            return {
                "status": "insufficient_data",
                "mode": "realtime_confirmed",
                "anchor_method": "confirmed_pivots",
                "reason": "Insufficient confirmed wave pivot points for real-time calculation"
            }

        # 尋找滿足 P0 < P1 順序與價格邏輯之有效轉折對
        valid_pair = None
        for low in reversed(low_pivots):
            for high in reversed(high_pivots):
                if low.pivot_index < high.pivot_index and low.pivot_price < high.pivot_price:
                    valid_pair = (low, high)
                    break
            if valid_pair:
                break

        if not valid_pair:
            return {
                "status": "insufficient_data",
                "mode": "realtime_confirmed",
                "anchor_method": "confirmed_pivots",
                "reason": "No valid low-to-high wave pivot sequence found"
            }

        last_low, last_high = valid_pair
        p0 = last_low.pivot_price
        p1 = last_high.pivot_price
        p2_proj = round(p1 - (p1 - p0) * 0.382, 2)

        targets = self.calculate_wave_targets(p0=p0, p1=p1, p2=p2_proj)
        time_win = self.check_time_window(df, pivot_date=last_low.pivot_date)

        return {
            "status": "available",
            "mode": "realtime_confirmed",
            "anchor_method": "confirmed_pivots",
            "p0": {"price": p0, "pivot_date": last_low.pivot_date, "confirmed_at": last_low.confirmed_at},
            "p1": {"price": p1, "pivot_date": last_high.pivot_date, "confirmed_at": last_high.confirmed_at},
            "p2": {
                "price": p2_proj,
                "type": "derived_projection",
                "formula": "p1 - 0.382 * (p1 - p0)",
                "pivot_date": None,
                "confirmed_at": None
            },
            "targets": targets,
            "time_window": time_win
        }

    def get_hindsight_wave_params(self, symbol: str, df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        事後圖表模式：使用全歷史區間極值描繪圖表，附帶明確 Warning 標記。
        零預設值：無資料時回傳 status: "insufficient_data"。
        """
        if df is not None and not df.empty and 'close' in df.columns:
            try:
                min_idx = df['close'].idxmin()
                p0 = float(df.loc[min_idx, 'close'])
                pivot_date = str(df.loc[min_idx, 'date'])
                
                after_p0_df = df.loc[min_idx:]
                p1 = float(after_p0_df['close'].max()) if not after_p0_df.empty else float(df['close'].max())
                p2 = round(p1 - (p1 - p0) * 0.382, 2)

                targets = self.calculate_wave_targets(p0=p0, p1=p1, p2=p2)
                time_win = self.check_time_window(df, pivot_date=pivot_date)
                
                return {
                    "status": "available",
                    "mode": "hindsight_visualization",
                    "anchor_method": "full_range_extrema",
                    "warning": "Uses full-range historical extrema and must not be interpreted as a real-time signal",
                    "p0": p0,
                    "p1": p1,
                    "p2": p2,
                    "pivot_date": pivot_date,
                    "targets": targets,
                    "time_window": time_win
                }
            except Exception as e:
                logger.error(f"事後圖表推導 {symbol} 點位失敗: {str(e)}")

        return {
            "status": "insufficient_data",
            "mode": "hindsight_visualization",
            "anchor_method": "none",
            "warning": "Uses full-range historical extrema and must not be interpreted as a real-time signal",
            "p0": None,
            "p1": None,
            "p2": None
        }

    def calculate_wave_targets(self, p0: float, p1: float, p2: Optional[float] = None) -> Dict[str, float]:
        wave1_diff = p1 - p0
        if p2 is None:
            p2 = p1 - wave1_diff * 0.618

        targets = {
            "p0": p0,
            "p1": p1,
            "p2": p2,
            "wave1_amplitude": round(wave1_diff, 2),
            "wave3_1.382": round(p2 + wave1_diff * 1.382, 2),
            "wave3_1.618": round(p2 + wave1_diff * 1.618, 2),
            "wave3_2.000": round(p2 + wave1_diff * 2.000, 2),
            "wave3_2.618": round(p2 + wave1_diff * 2.618, 2),
            "wave5_1.000": round(p2 + wave1_diff * 1.618 + wave1_diff * 1.0, 2),
            "wave5_3.236": round(p0 + wave1_diff * 3.236, 2)
        }
        return targets

    def check_time_window(self, df: pd.DataFrame, pivot_date: str, is_monthly: bool = False) -> Dict[str, Any]:
        if df.empty or 'date' not in df.columns:
            return {"is_in_window": False, "reason": "DataFrame 為空或無 date 欄位"}

        fib_numbers = self.config.get("fibonacci", {}).get("numbers", self.DEFAULT_FIB_NUMBERS)
        df_sorted = df.sort_values("date").reset_index(drop=True)
        latest_date_str = df_sorted["date"].iloc[-1]
        
        if is_monthly:
            p_dt = datetime.strptime(pivot_date, "%Y-%m-%d")
            l_dt = datetime.strptime(latest_date_str, "%Y-%m-%d")
            elapsed_units = (l_dt.year - p_dt.year) * 12 + (l_dt.month - p_dt.month)
            unit_name = "月"
        else:
            sub_df = df_sorted[df_sorted["date"] >= pivot_date]
            elapsed_units = len(sub_df)
            unit_name = "個交易日"

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
