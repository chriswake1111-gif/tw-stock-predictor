import os
import yaml
import logging
import pandas as pd
import numpy as np
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class MADeductionEngine:
    """費氏多空均線群計算、扣抵斜率預判與多空共振檢測器"""

    DEFAULT_MA_PERIODS = [8, 13, 21, 55, 144, 233]
    MODEL_CLASSIFICATION = {
        "model_version": "1.x",
        "legacy": True,
        "rule_ids": ["MA-01", "MA-02", "MA-03"],
        "evidence_level": "U",
        "implementation_mode": "legacy_experimental",
        "official_affiliation": False,
    }

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.ma_periods = self._load_ma_periods()

    def _load_ma_periods(self) -> List[int]:
        """從 YAML 設定檔載入費氏均線天數設定"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                    return cfg.get("fibonacci", {}).get("ma_periods", self.DEFAULT_MA_PERIODS)
            except Exception as e:
                logger.error(f"讀取 {self.config_path} 失敗: {str(e)}")
        return self.DEFAULT_MA_PERIODS

    def calculate_ma_and_deductions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        全向量化運算 (Pandas Vectorized Ops)：
        1. 清理未收盤之 NaN 價格
        2. 計算費氏均線群 (SMA 8, 13, 21, 55, 144, 233)
        3. 計算扣抵值 (deduct_val_N = shift(N-1))
        4. 預判明日均線斜率 (ma_slope_up_N = close > deduct_val_N)
        """
        if df.empty or 'close' not in df.columns:
            logger.warning("輸入的 DataFrame 為空或無 'close' 欄位")
            return df

        # 過濾包含 NaN 的收盤價（如當前尚未收盤的 K 線）
        res_df = df.dropna(subset=["close"]).copy()

        # 向量化計算各天期 SMA 與扣抵比對
        for N in self.ma_periods:
            # 1. 均線值 (Simple Moving Average)
            res_df[f"SMA_{N}"] = res_df["close"].rolling(window=N).mean()
            
            # 2. 明日扣抵值 (N 日前對應的 K 線收盤價 P(t-N+1))
            res_df[f"deduct_val_{N}"] = res_df["close"].shift(N - 1)
            
            # 3. 均線斜率向上預判：當前股價 > 扣抵值 => 均線斜率向上 (多頭支撐)
            res_df[f"ma_slope_up_{N}"] = res_df["close"] > res_df[f"deduct_val_{N}"]

        return res_df

    def detect_resonance_signal(self, df: pd.DataFrame) -> pd.Series:
        """Legacy v1 experimental resonance utility (Rule MA-03, evidence U).

        This method remains callable for v1 compatibility and must not be used
        as verified core, a Du-method claim, or an automatic-order signal.
        多空共振檢測器：
        當短、中、長天期均線 (8, 13, 21, 55, 144) 同時滿足：
        1. 所有均線扣抵向上 (ma_slope_up_N == True)
        2. 均線多頭排列 (SMA_8 > SMA_13 > SMA_21 > SMA_55)
        :return: 布林 Series
        """
        if df.empty:
            return pd.Series(False, index=df.index, dtype=bool)

        required_columns = {
            "SMA_8", "SMA_13", "SMA_21", "SMA_55",
            "ma_slope_up_8", "ma_slope_up_13", "ma_slope_up_21",
            "ma_slope_up_55", "ma_slope_up_144"
        }
        if not required_columns.issubset(df.columns) and "close" in df.columns:
            df = self.calculate_ma_and_deductions(df)

        if not required_columns.issubset(df.columns):
            logger.warning("均線扣抵欄位不完整，依 fail-closed 原則不產生共振訊號")
            return pd.Series(False, index=df.index, dtype=bool)

        # 1. 扣抵全向上條件
        deduct_all_up = (
            df["ma_slope_up_8"] &
            df["ma_slope_up_13"] &
            df["ma_slope_up_21"] &
            df["ma_slope_up_55"] &
            df["ma_slope_up_144"]
        )

        # 2. 均線多頭排列條件
        bullish_alignment = (
            (df["SMA_8"] > df["SMA_13"]) &
            (df["SMA_13"] > df["SMA_21"]) &
            (df["SMA_21"] > df["SMA_55"])
        )

        resonance = deduct_all_up & bullish_alignment
        return resonance.fillna(False)
