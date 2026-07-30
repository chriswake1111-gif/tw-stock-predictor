import os
import yaml
import logging
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class CapitalAllocator:
    """台股 20/30/50 資金分批配置、張數換算與跳空防禦風控模組"""

    def __init__(self, total_cash: float = 1000000.0, config_path: str = "config/config.yaml"):
        self.total_cash = total_cash
        self.config_path = config_path

    def calculate_position_size(self, price: float, target_ratio: float, current_cash: Optional[float] = None, lot_size: int = 1000) -> int:
        """
        將指定比例 (如 20%, 30%, 50%) 的資金自動換算為台股整張 (1,000 股倍數) 下單數量
        :param price: 當前股價
        :param target_ratio: 資金配置比例 (如 0.20 代表 20%)
        :param current_cash: 當前可用現金 (若未指定採總資產)
        :param lot_size: 每張股數 (預設 1000 股)
        :return: 下單總股數 (1,000 股之倍數)
        """
        if price <= 0 or target_ratio <= 0:
            return 0

        available_cash = current_cash if current_cash is not None else self.total_cash
        target_amount = available_cash * target_ratio
        cost_per_lot = price * lot_size
        num_lots = int(target_amount // cost_per_lot)
        
        # 若單價高昂（如高價股台積電），20% 資金不足 1 張，但可用現金足夠買 1 張時，配備至少 1 張 (1,000 股)
        if num_lots == 0 and available_cash >= cost_per_lot:
            num_lots = 1

        return num_lots * lot_size

    def check_gap_down_defense(self, open_price: float, prev_close: float, gap_threshold: float = 0.015) -> bool:
        """
        跳空開盤防禦機制：
        當今日開盤價相比前一日收盤價向下跳空幅度 > 1.5% 時，觸發延後進場機制 (避開開盤接刀風險)。
        :return: True 代表安全可進場；False 代表觸發跳空防禦，暫延進場
        """
        if prev_close <= 0:
            return True

        gap_down_ratio = (prev_close - open_price) / prev_close
        if gap_down_ratio > gap_threshold:
            return False

        return True

    def check_risk_deleverage(self, overheat_volume: bool = False, overheat_margin: bool = False) -> bool:
        """
        過熱風控自動減碼觸發檢測：
        當成交量/M1B 天量過熱或全市場融資過熱時，觸發減碼 50% 或平倉訊號。
        :return: True 代表需減碼/避險
        """
        if overheat_volume or overheat_margin:
            return True
        return False
