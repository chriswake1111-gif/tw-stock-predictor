import logging
import math
from typing import Dict, Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class CapitalAllocator:
    """
    台股張數與 20/30/50 進場週期基準 (Campaign-Base) 資金算術管理器：
    1. 以進場時點之帳戶總資產 (Campaign Base Value) 為唯一計算基準。
    2. 計算「累計目標持倉張數」減去「當前持倉張數」，求出精確下單張數。
    3. 實作台股整張 (1,000 股) 倍數轉換與 0.5% 現金/滑價/手續費安全緩衝防禦。
    """

    def __init__(self, lot_size: int = 1000, cash_buffer_rate: float = 0.005):
        self.lot_size = lot_size
        self.cash_buffer_rate = cash_buffer_rate

    def calculate_order_size(
        self,
        price: float,
        target_cumulative_ratio: float,
        base_value: float,
        current_position_size: int = 0,
        available_cash: float = 0.0
    ) -> int:
        """
        計算本次需要買進的增量股票股數 (必須為 1,000 股的倍數)
        :param price: 當前單股價格
        :param target_cumulative_ratio: 累計目標持倉比例 (Stage 1: 0.20, Stage 2: 0.50, Stage 3: 1.00)
        :param base_value: 進場週期基準金額 (Campaign Base Value)
        :param current_position_size: 當前帳戶已持有之股票股數
        :param available_cash: 當前可用現金 (可用於現金緩衝比對)
        """
        if price <= 0 or base_value <= 0:
            return 0

        # 1. 計算目標總持倉市值與目標總股數 (無條件捨去至整張 1,000 股)
        target_position_value = base_value * target_cumulative_ratio
        target_total_shares = math.floor(target_position_value / (price * self.lot_size)) * self.lot_size

        # 2. 計算本次需要增加的增量股數
        needed_shares = max(0, target_total_shares - current_position_size)
        if needed_shares < self.lot_size:
            return 0

        # 3. 現金與費用緩衝保護 (含 0.5% 手續費與滑價緩衝)
        required_cash = needed_shares * price * (1.0 + self.cash_buffer_rate)
        if available_cash > 0 and required_cash > available_cash:
            # 當現金不足時，無條件往下縮減至最大可用整張數
            max_affordable_shares = math.floor(available_cash / (price * (1.0 + self.cash_buffer_rate) * self.lot_size)) * self.lot_size
            needed_shares = min(needed_shares, max_affordable_shares)

        return max(0, needed_shares)
