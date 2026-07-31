import os
import yaml
import logging
import backtrader as bt
import pandas as pd
from typing import Dict, Any, List, Optional

from src.strategy.capital_allocation import CapitalAllocator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class TWSalesTaxCommissionScheme(bt.CommissionInfo):
    """台股真實交易成本 Scheme：買進 0.1425% 手續費，賣出 0.1425% 手續費 + 0.3% 證交稅"""
    params = (
        ('stocklike', True),
        ('commtype', bt.CommissionInfo.COMM_PERC),
        ('percabs', True),
        ('commission', 0.001425),
        ('tax', 0.003),
    )

    def _getcommission(self, size, price, pseudoexec):
        value = abs(size) * price
        comm = value * self.p.commission
        if size < 0:
            comm += value * self.p.tax
        return comm

class TuStrategy(bt.Strategy):
    """杜金龍 20/30/50 浪 3 主升段量化回測策略 (含異步生命週期與資金基準鎖)"""
    
    params = (
        ('gap_threshold', -0.015),
        ('config_path', 'config/config.yaml'),
    )

    def __init__(self):
        # 均線指標
        self.sma8 = bt.indicators.SimpleMovingAverage(self.data.close, period=8)
        self.sma13 = bt.indicators.SimpleMovingAverage(self.data.close, period=13)
        self.sma21 = bt.indicators.SimpleMovingAverage(self.data.close, period=21)
        self.sma55 = bt.indicators.SimpleMovingAverage(self.data.close, period=55)
        self.sma144 = bt.indicators.SimpleMovingAverage(self.data.close, period=144)
        
        # 量能均線指標 (真正 5 日均量與 20 日均量)
        self.sma5_vol = bt.indicators.SimpleMovingAverage(self.data.volume, period=5)
        self.sma20_vol = bt.indicators.SimpleMovingAverage(self.data.volume, period=20)

        # 資金管理器
        self.allocator = CapitalAllocator(lot_size=1000, cash_buffer_rate=0.005)

        # 訂單異步生命週期鎖
        self.pending_order = None
        self.pending_stage = None
        self.pending_campaign_base_value = None

        # 策略階段與資金基準
        self.stage = 0
        self.campaign_base_value = None

        # 浪 3 拉回狀態追蹤
        self.campaign_high = None
        self.pre_pullback_high = None
        self.pullback_low = None
        self.pullback_detected = False
        self.pullback_invalidated = False

        # 精確交易日誌
        self.execution_log = []

    def notify_order(self, order):
        """Backtrader 訂單狀態變更通知生命週期"""
        if order.status == order.Completed:
            # 1. 異步更新 Stage
            if self.pending_stage is not None:
                self.stage = self.pending_stage
            
            # 2. 資金基準轉正
            if self.stage == 1:
                self.campaign_base_value = self.pending_campaign_base_value
            elif self.stage == 0 and self.position.size == 0:
                self.reset_campaign_state()

            # 3. 紀錄精確交易日誌
            dt_str = self.data.datetime.date(0).isoformat()
            self.execution_log.append({
                "date": dt_str,
                "action": "buy" if order.isbuy() else "sell",
                "stage_after": self.stage,
                "size": order.executed.size,
                "price": order.executed.price,
                "value": order.executed.value,
                "commission": order.executed.comm
            })

        # 4. 無論 Completed/Canceled/Margin/Rejected，一律解鎖
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.pending_order = None
            self.pending_stage = None
            self.pending_campaign_base_value = None

    def reset_campaign_state(self):
        """平倉完成後清除進場週期狀態"""
        self.campaign_base_value = None
        self.campaign_high = None
        self.pre_pullback_high = None
        self.pullback_low = None
        self.pullback_detected = False
        self.pullback_invalidated = False

    def next(self):
        # 1. 有未完成訂單時，絕不重複送單
        if self.pending_order is not None:
            return

        close_val = self.data.close[0]
        open_val = self.data.open[0]
        prev_close = self.data.close[-1]

        # 開盤跳空防衛
        open_gap = (open_val - prev_close) / prev_close if prev_close > 0 else 0.0
        safe_open = open_gap >= self.p.gap_threshold

        # 均線多空共振
        is_resonance = (self.sma8[0] > self.sma13[0] > self.sma21[0] > self.sma55[0])

        # 2. 全域風控與退場判定 (跌破 SMA55 或死亡交叉)
        if self.position.size > 0:
            stop_loss_triggered = (close_val < self.sma55[0]) or (self.sma8[0] < self.sma21[0])
            if stop_loss_triggered:
                self.pending_order = self.close()
                self.pending_stage = 0
                return

        # 3. 階段進場與加碼
        if self.stage == 0 and is_resonance and safe_open:
            proposed_base = self.broker.getvalue()
            size = self.allocator.calculate_order_size(
                price=close_val,
                target_cumulative_ratio=0.20,
                base_value=proposed_base,
                current_position_size=self.position.size,
                available_cash=self.broker.getcash()
            )
            if size >= 1000:
                self.pending_campaign_base_value = proposed_base
                self.pending_order = self.buy(size=size)
                self.pending_stage = 1

        elif self.stage == 1:
            # 追蹤浪 3 頂點與拉回幅度
            self.campaign_high = max(self.campaign_high or self.data.high[0], self.data.high[0])
            pullback_pct = (self.campaign_high - close_val) / self.campaign_high

            if pullback_pct > 0.11:
                self.pullback_invalidated = True

            # 拉回 7%~11% 且守住 SMA55 時加碼 Stage 2
            if not self.pullback_invalidated and 0.07 <= pullback_pct <= 0.11 and close_val > self.sma55[0]:
                if not self.pullback_detected:
                    self.pre_pullback_high = self.campaign_high
                    self.pullback_detected = True

                size = self.allocator.calculate_order_size(
                    price=close_val,
                    target_cumulative_ratio=0.50,
                    base_value=self.campaign_base_value,
                    current_position_size=self.position.size,
                    available_cash=self.broker.getcash()
                )
                if size >= 1000:
                    self.pending_order = self.buy(size=size)
                    self.pending_stage = 2

        elif self.stage == 2:
            # 突破凍結前高且 5 日均量 > 20 日均量時加碼 Stage 3
            is_breakout = (self.pre_pullback_high is not None) and (close_val > self.pre_pullback_high)
            volume_confirm = self.sma5_vol[0] > self.sma20_vol[0]

            if is_breakout and volume_confirm:
                size = self.allocator.calculate_order_size(
                    price=close_val,
                    target_cumulative_ratio=1.00,
                    base_value=self.campaign_base_value,
                    current_position_size=self.position.size,
                    available_cash=self.broker.getcash()
                )
                if size >= 1000:
                    self.pending_order = self.buy(size=size)
                    self.pending_stage = 3

class TuBacktester:
    """杜金龍策略歷史回測執行器"""

    def __init__(self, initial_cash: float = 1000000.0, config_path: str = "config/config.yaml"):
        self.initial_cash = initial_cash
        self.config_path = config_path

    def run_backtest(self, ohlcv_df: pd.DataFrame) -> Dict[str, Any]:
        if ohlcv_df.empty or len(ohlcv_df) < 144:
            return {"error": "K線資料不足 (需至少 144 筆以上)"}

        df = ohlcv_df.dropna(subset=['open', 'high', 'low', 'close', 'volume']).copy()
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)

        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df, datetime=None)
        cerebro.adddata(data)

        cerebro.addstrategy(TuStrategy, config_path=self.config_path)
        cerebro.broker.setcash(self.initial_cash)
        
        # 掛載台股交易成本 Scheme (手續費 + 證交稅)
        comm_scheme = TWSalesTaxCommissionScheme()
        cerebro.broker.addcommissioninfo(comm_scheme)

        # 0.1% 滑價比率設定
        cerebro.broker.set_slippage_perc(perc=0.001, slip_open=True, slip_match=True)

        # 分析器
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trade_analyzer')
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe_ratio', riskfreerate=0.01)

        results = cerebro.run()
        strat = results[0]

        final_value = cerebro.broker.getvalue()
        total_return_pct = ((final_value - self.initial_cash) / self.initial_cash) * 100.0

        drawdown = strat.analyzers.drawdown.get_analysis()
        max_drawdown = drawdown.get('max', {}).get('drawdown', 0.0)

        trade_info = strat.analyzers.trade_analyzer.get_analysis()
        total_trades = trade_info.get('total', {}).get('closed', 0)
        won_trades = trade_info.get('won', {}).get('total', 0)
        win_rate = (won_trades / total_trades * 100.0) if total_trades > 0 else 0.0

        sharpe_info = strat.analyzers.sharpe_ratio.get_analysis()
        sharpe_ratio = sharpe_info.get('sharperatio', 0.0)

        return {
            "initial_cash": self.initial_cash,
            "final_value": round(final_value, 2),
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 2),
            "sharpe_ratio": round(sharpe_ratio, 2) if sharpe_ratio is not None else 0.0,
            "execution_log": strat.execution_log
        }
