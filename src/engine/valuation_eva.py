import os
import yaml
import logging
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class ValuationEVAEngine:
    """杜金龍基本面估值、EVA長線底盤與二低一高/破底翻選股引擎"""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"讀取 {self.config_path} 失敗: {str(e)}")
        return {}

    def estimate_future_eps(
        self, 
        institutional_eps: Optional[float] = None, 
        historical_ttm_eps: Optional[float] = None, 
        growth_rate: Optional[float] = None
    ) -> float:
        """Legacy v1 estimate retained for compatibility.

        Historical TTM growth output is not Forward EPS and may not be used by
        the v2 verified valuation core.
        預估 EPS 雙軌機制 (Dual-track EPS):
        軌道 1: 若提供法人預估 EPS，優先採用法人預估值。
        軌道 2: 備援機制 = 歷史 TTM EPS * (1 + 預設成長率).
        """
        if institutional_eps is not None and institutional_eps > 0:
            return float(institutional_eps)
        
        if historical_ttm_eps is not None and historical_ttm_eps > 0:
            if growth_rate is None:
                growth_rate = self.config.get("valuation", {}).get("default_eps_growth", 0.10)
            return float(historical_ttm_eps * (1.0 + growth_rate))

        return 0.0

    def calculate_dog_master_valuation(
        self, 
        eps: float, 
        pe_min: float = 10.0, 
        pe_mid: float = 20.0, 
        pe_max: float = 25.0
    ) -> Dict[str, Any]:
        """
        主人與小狗估值模型 (Dog & Master Valuation Model):
        合理目標價 = 預估未來 EPS * 歷史平均 PE (便宜/合理/昂貴價位)
        """
        val_cfg = self.config.get("valuation", {})
        pe_min = val_cfg.get("pe_min", pe_min)
        pe_mid = val_cfg.get("pe_mid", pe_mid)
        pe_max = val_cfg.get("pe_max", pe_max)

        if eps <= 0:
            return {
                "status": "not_applicable",
                "reason": "PE valuation is not applicable when EPS is zero or negative",
                "estimated_eps": round(float(eps), 2),
                "pe_min": pe_min,
                "pe_mid": pe_mid,
                "pe_max": pe_max,
                "cheap_price": None,
                "fair_price": None,
                "expensive_price": None
            }

        if not (0 < pe_min <= pe_mid <= pe_max):
            raise ValueError("PE multiples must be positive and ordered pe_min <= pe_mid <= pe_max")

        cheap_price = round(eps * pe_min, 2)
        fair_price = round(eps * pe_mid, 2)
        expensive_price = round(eps * pe_max, 2)

        return {
            "status": "available",
            "estimated_eps": round(eps, 2),
            "pe_min": pe_min,
            "pe_mid": pe_mid,
            "pe_max": pe_max,
            "cheap_price": cheap_price,
            "fair_price": fair_price,
            "expensive_price": expensive_price
        }

    def calculate_eva_floor(
        self, 
        nopat: float, 
        invested_capital: float, 
        wacc: Optional[float] = None,
        total_shares_billion: float = 1.0
    ) -> Dict[str, Any]:
        """
        EVA 長線價值底盤模型:
        EVA = NOPAT - (Invested Capital * WACC)
        價值底盤估值 = (Invested Capital + EVA / WACC) / 股數
        :param nopat: 稅後淨利 (億元)
        :param invested_capital: 投入資本 (億元)
        :param wacc: 加權平均資金成本 (若未提供則由 config 載入 7%)
        :param total_shares_billion: 總股數 (億股)
        """
        if wacc is None:
            wacc = self.config.get("valuation", {}).get("default_wacc", 0.07)

        if wacc <= 0 or total_shares_billion <= 0:
            return {
                "status": "not_applicable",
                "reason": "WACC and total shares must both be greater than zero",
                "eva_floor_price": None
            }

        eva_billion = round(nopat - (invested_capital * wacc), 2)
        total_enterprise_val = invested_capital + (eva_billion / wacc)
        eva_floor_price = round(total_enterprise_val / total_shares_billion, 2)

        return {
            "status": "available",
            "model_version": "1.x",
            "legacy": True,
            "rule_id": "VAL-05",
            "evidence_level": "U",
            "implementation_mode": "unsupported",
            "official_affiliation": False,
            "nopat_billion": nopat,
            "invested_capital_billion": invested_capital,
            "wacc": wacc,
            "eva_billion": eva_billion,
            "eva_floor_price": eva_floor_price
        }

    def screen_two_lows_one_high(
        self, 
        pe: float, 
        pb: float, 
        yield_rate: float,
        pe_market_avg: float = 15.0
    ) -> Dict[str, Any]:
        """
        二低一高選股器:
        條件: PE < 市場平均 (或 < 15) AND PB < 1.5 AND 殖利率 > 4%
        """
        val_cfg = self.config.get("valuation", {})
        pb_max = val_cfg.get("pb_max", 1.5)
        yield_min = val_cfg.get("yield_min", 0.04)

        c1 = pe < pe_market_avg
        c2 = pb < pb_max
        c3 = yield_rate >= yield_min

        passed = bool(c1 and c2 and c3)
        return {
            "passed": passed,
            "pe": pe,
            "pb": pb,
            "yield_rate": yield_rate,
            "yield_unit": "ratio",
            "conditions": {
                "pe_below_avg": bool(c1),
                "pb_below_max": bool(c2),
                "yield_above_min": bool(c3)
            }
        }

    def detect_breakout_reversal(
        self, 
        df: pd.DataFrame, 
        lookback_days: int = 5, 
        recovery_days: int = 3
    ) -> pd.Series:
        """
        破底翻技術型態強訊號 (Breakout Reversal Pattern):
        當股價跌破近 Lookback 天數底線後，在 Recovery 天數內放量快速收復原先支撐線。
        """
        if df.empty or 'close' not in df.columns or 'volume' not in df.columns:
            return pd.Series([False]*len(df), index=df.index)

        close = df['close']
        volume = df['volume']

        # 1. 計算過去 Lookback 天的歷史低點支撐線
        support_level = close.shift(1).rolling(window=lookback_days).min()
        
        # 2. 判定是否有跌破歷史支撐
        is_breakdown = close < support_level

        # 3. 在近 recovery_days 天內是否有發生過跌破
        recent_breakdown = is_breakdown.shift(1).rolling(window=recovery_days).max() == 1.0

        # 4. 今日價格強勢收復支撐線 (close >= support_level.shift(1)) 且放量 (volume > volume.shift(1))
        ref_support = support_level.shift(1)
        recovered = close >= ref_support
        volume_up = volume > volume.shift(1)

        breakout_reversal = recent_breakdown & recovered & volume_up
        return breakout_reversal.fillna(False)
    MODEL_CLASSIFICATION = {
        "model_version": "1.x",
        "legacy": True,
        "official_affiliation": False,
        "future_eps_estimate": {
            "implementation_mode": "legacy_experimental",
            "forbidden_uses": ["forward_eps", "verified_core"],
        },
        "eva_floor": {
            "rule_id": "VAL-05",
            "evidence_level": "U",
            "implementation_mode": "unsupported",
        },
    }
