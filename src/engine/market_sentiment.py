import os
import yaml
import logging
import pandas as pd
from typing import Dict, Any, Optional
from src.collectors.cbc_collector import CBCCollector

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class MarketSentimentEngine:
    """總體籌碼與大盤市場過熱風險控管引擎 (M1B 頭部過熱與融資槓桿溫度計)"""

    def __init__(self, db_path: str = "data/cache.db", config_path: str = "config/config.yaml"):
        self.db_path = db_path
        self.config_path = config_path
        self.config = self._load_config()
        self.cbc_collector = CBCCollector(db_path=self.db_path, config_path=self.config_path)

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"讀取 {self.config_path} 失敗: {str(e)}")
        return {}

    def check_volume_m1b_overheat(
        self, 
        daily_volume_billion: float, 
        m1b_billion: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        大盤頭部天量預警 (成交總金額 / M1B 比率)：
        :param daily_volume_billion: 單日上市櫃總成交金額 (億元)
        :param m1b_billion: 央行最新 M1B 貨幣供給量 (億元)
        """
        if m1b_billion is None or m1b_billion <= 0:
            m1b_billion = self.cbc_collector.get_latest_m1b()

        sentiment_cfg = self.config.get("sentiment", {})
        threshold_ratio = sentiment_cfg.get("volume_m1b_threshold", 0.020)

        # 比率計算
        ratio = round(daily_volume_billion / m1b_billion, 4)
        
        # 單日爆出天量判斷 (例如單日超過 2.5 兆 = 25000 億) 或 比率 > 臨界門檻 (2%)
        is_extreme_volume = daily_volume_billion >= 25000.0
        is_ratio_overheat = ratio >= threshold_ratio

        is_overheat = is_extreme_volume or is_ratio_overheat

        status_msg = (
            f"大盤成交金額 {daily_volume_billion} 億 / M1B {m1b_billion} 億 (比率: {ratio*100:.2f}%) " +
            ("[警告: 市場陷入極端過熱狂熱狀態!]" if is_overheat else "[正常: 大盤成交量能維繫在適中區間]")
        )

        return {
            "daily_volume_billion": daily_volume_billion,
            "m1b_billion": m1b_billion,
            "volume_m1b_ratio": ratio,
            "threshold_ratio": threshold_ratio,
            "is_overheat": is_overheat,
            "status_message": status_msg
        }

    def check_margin_leverage_heat(self, margin_return: float) -> Dict[str, Any]:
        """
        槓桿過熱指標 (融資報酬率 / 全市場槓桿溫度計)：
        當融資報酬率增幅 > 8% 時觸發減碼避險訊號。
        :param margin_return: 近期融資報酬率 (如 0.10 代表 10%)
        """
        sentiment_cfg = self.config.get("sentiment", {})
        threshold = sentiment_cfg.get("margin_return_threshold", 0.08)

        is_heat_warning = margin_return >= threshold
        status_msg = (
            f"全市場融資報酬率增幅: {margin_return*100:.2f}% (門檻: {threshold*100:.2f}%) " +
            ("[過熱警示: 融資槓桿極度浮濫，觸發分批減碼訊號!]" if is_heat_warning else "[正常: 融資槓桿處於健康風險天數]")
        )

        return {
            "margin_return": margin_return,
            "threshold": threshold,
            "is_heat_warning": is_heat_warning,
            "status_message": status_msg
        }
