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
    """僅供歷史回測的 20/30/50 模擬策略，不具任何真實委託能力。"""
    
    params = (
        ('config_path', 'config/config.yaml'),
        ('trade_start_date', None),
        ('lot_size', 1000),
        ('stage1_ratio', 0.20),
        ('stage2_ratio', 0.50),
        ('stage3_ratio', 1.00),
        ('pullback_min_pct', 0.07),
        ('pullback_max_pct', 0.11),
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
        self.allocator = CapitalAllocator(lot_size=self.p.lot_size, cash_buffer_rate=0.005)

        # 訂單異步生命週期鎖
        self.pending_order = None
        self.pending_stage = None
        self.pending_campaign_base_value = None
        self.pending_signal_date = None

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
        self.equity_curve = []
        self.bars_evaluated = 0
        self.position_days = 0

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
                "signal_date": self.pending_signal_date,
                "execution_date": dt_str,
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
            self.pending_signal_date = None

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

        current_date = self.data.datetime.date(0).isoformat()
        if self.p.trade_start_date and current_date < str(self.p.trade_start_date):
            return

        self.bars_evaluated += 1
        if self.position.size > 0:
            self.position_days += 1
        close_val = self.data.close[0]
        self.equity_curve.append({
            "date": current_date,
            "equity": float(self.broker.getvalue()),
            "cash": float(self.broker.getcash()),
            "position_value": float(abs(self.position.size) * close_val),
            "gross_exposure_pct": round(
                float(abs(self.position.size) * close_val)
                / float(self.broker.getvalue())
                * 100.0,
                4,
            ) if self.broker.getvalue() > 0 else 0.0,
        })

        # 與 MADeductionEngine 相同的「明日扣抵」契約：目前收盤價高於 N-1 日前收盤價。
        deduction_all_up = all(
            close_val > self.data.close[-(period - 1)]
            for period in [8, 13, 21, 55, 144]
        )
        bullish_alignment = self.sma8[0] > self.sma13[0] > self.sma21[0] > self.sma55[0]
        is_resonance = bool(deduction_all_up and bullish_alignment)

        # 2. 全域風控與退場判定 (跌破 SMA55 或死亡交叉)
        if self.position.size > 0:
            stop_loss_triggered = (close_val < self.sma55[0]) or (self.sma8[0] < self.sma21[0])
            if stop_loss_triggered:
                self.pending_signal_date = current_date
                self.pending_order = self.close()
                self.pending_stage = 0
                return

        # 3. 階段進場與加碼
        if self.stage == 0 and is_resonance:
            proposed_base = self.broker.getvalue()
            size = self.allocator.calculate_order_size(
                price=close_val,
                target_cumulative_ratio=self.p.stage1_ratio,
                base_value=proposed_base,
                current_position_size=self.position.size,
                available_cash=self.broker.getcash()
            )
            if size >= self.p.lot_size:
                self.pending_campaign_base_value = proposed_base
                self.pending_signal_date = current_date
                self.pending_order = self.buy(size=size)
                self.pending_stage = 1

        elif self.stage == 1:
            # 追蹤浪 3 頂點與拉回幅度
            self.campaign_high = max(self.campaign_high or self.data.high[0], self.data.high[0])
            pullback_pct = (self.campaign_high - close_val) / self.campaign_high

            if pullback_pct > self.p.pullback_max_pct:
                self.pullback_invalidated = True

            # 拉回 7%~11% 且守住 SMA55 時加碼 Stage 2
            if (
                not self.pullback_invalidated
                and self.p.pullback_min_pct <= pullback_pct <= self.p.pullback_max_pct
                and close_val > self.sma55[0]
            ):
                if not self.pullback_detected:
                    self.pre_pullback_high = self.campaign_high
                    self.pullback_detected = True

                size = self.allocator.calculate_order_size(
                    price=close_val,
                    target_cumulative_ratio=self.p.stage2_ratio,
                    base_value=self.campaign_base_value,
                    current_position_size=self.position.size,
                    available_cash=self.broker.getcash()
                )
                if size >= self.p.lot_size:
                    self.pending_signal_date = current_date
                    self.pending_order = self.buy(size=size)
                    self.pending_stage = 2

        elif self.stage == 2:
            # 突破凍結前高且 5 日均量 > 20 日均量時加碼 Stage 3
            is_breakout = (self.pre_pullback_high is not None) and (close_val > self.pre_pullback_high)
            volume_confirm = self.sma5_vol[0] > self.sma20_vol[0]

            if is_breakout and volume_confirm:
                size = self.allocator.calculate_order_size(
                    price=close_val,
                    target_cumulative_ratio=self.p.stage3_ratio,
                    base_value=self.campaign_base_value,
                    current_position_size=self.position.size,
                    available_cash=self.broker.getcash()
                )
                if size >= self.p.lot_size:
                    self.pending_signal_date = current_date
                    self.pending_order = self.buy(size=size)
                    self.pending_stage = 3

class TuBacktester:
    """杜金龍策略歷史回測執行器"""

    def __init__(
        self,
        initial_cash: float = 1000000.0,
        config_path: str = "config/config.yaml",
        commission_rate: float = 0.001425,
        sales_tax_rate: float = 0.003,
        slippage_rate: float = 0.001,
        lot_size: int = 1000,
        strategy_params: Optional[Dict[str, float]] = None,
    ):
        self.initial_cash = initial_cash
        self.config_path = config_path
        self.commission_rate = commission_rate
        self.sales_tax_rate = sales_tax_rate
        self.slippage_rate = slippage_rate
        self.lot_size = lot_size
        self.strategy_params = strategy_params or {}
        allowed_strategy_params = {
            "stage1_ratio",
            "stage2_ratio",
            "stage3_ratio",
            "pullback_min_pct",
            "pullback_max_pct",
        }
        unknown = set(self.strategy_params) - allowed_strategy_params
        if unknown:
            raise ValueError(f"不支援的策略參數: {sorted(unknown)}")
        stage1 = float(self.strategy_params.get("stage1_ratio", 0.20))
        stage2 = float(self.strategy_params.get("stage2_ratio", 0.50))
        stage3 = float(self.strategy_params.get("stage3_ratio", 1.00))
        pullback_min = float(self.strategy_params.get("pullback_min_pct", 0.07))
        pullback_max = float(self.strategy_params.get("pullback_max_pct", 0.11))
        if not (0 < stage1 <= stage2 <= stage3 <= 1.0):
            raise ValueError("策略持倉比例必須滿足 0 < stage1 <= stage2 <= stage3 <= 1")
        if not (0 <= pullback_min < pullback_max < 1.0):
            raise ValueError("拉回參數必須滿足 0 <= min < max < 1")

    def run_backtest(
        self,
        ohlcv_df: pd.DataFrame,
        trade_start_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        if ohlcv_df.empty or len(ohlcv_df) < 144:
            return {"error": "K線資料不足 (需至少 144 筆以上)"}

        df = ohlcv_df.dropna(subset=['open', 'high', 'low', 'close', 'volume']).copy()
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)

        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df, datetime=None)
        cerebro.adddata(data)

        cerebro.addstrategy(
            TuStrategy,
            config_path=self.config_path,
            trade_start_date=trade_start_date,
            lot_size=self.lot_size,
            **self.strategy_params,
        )
        cerebro.broker.setcash(self.initial_cash)
        
        # 掛載台股交易成本 Scheme (手續費 + 證交稅)
        comm_scheme = TWSalesTaxCommissionScheme(
            commission=self.commission_rate,
            tax=self.sales_tax_rate,
        )
        cerebro.broker.addcommissioninfo(comm_scheme)

        # 0.1% 滑價比率設定
        cerebro.broker.set_slippage_perc(
            perc=self.slippage_rate, slip_open=True, slip_match=True
        )

        # 分析器
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trade_analyzer')
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe_ratio', riskfreerate=0.01)

        results = cerebro.run()
        strat = results[0]

        final_value = cerebro.broker.getvalue()
        execution_log = list(strat.execution_log)
        terminal_liquidation_cost = 0.0
        if strat.position.size > 0:
            last_date = df.index[-1].date().isoformat()
            last_close = float(df["close"].iloc[-1])
            position_value = float(strat.position.size) * last_close
            terminal_liquidation_cost = position_value * (
                self.slippage_rate + self.commission_rate + self.sales_tax_rate
            )
            final_value -= terminal_liquidation_cost
            execution_log.append({
                "date": last_date,
                "signal_date": last_date,
                "execution_date": last_date,
                "action": "sell_terminal_liquidation",
                "stage_after": 0,
                "size": -int(strat.position.size),
                "price": round(last_close * (1.0 - self.slippage_rate), 4),
                "value": round(position_value * (1.0 - self.slippage_rate), 2),
                "commission": round(
                    position_value * (self.commission_rate + self.sales_tax_rate), 2
                ),
            })
            if strat.equity_curve:
                strat.equity_curve[-1]["equity"] = final_value
        total_return_pct = ((final_value - self.initial_cash) / self.initial_cash) * 100.0

        equity_values = pd.Series(
            [item["equity"] for item in strat.equity_curve], dtype=float
        )
        if equity_values.empty:
            max_drawdown = 0.0
            calculated_sharpe = None
        else:
            peaks = equity_values.cummax()
            max_drawdown = abs(float(((equity_values / peaks) - 1.0).min() * 100.0))
            daily_returns = equity_values.pct_change().replace(
                [float("inf"), float("-inf")], float("nan")
            ).dropna()
            if len(daily_returns) >= 2 and float(daily_returns.std(ddof=1)) > 0:
                calculated_sharpe = float(daily_returns.mean() / daily_returns.std(ddof=1) * (252 ** 0.5))
            else:
                calculated_sharpe = None

        closed_trade_returns = []
        campaign_cost = 0.0
        for log in execution_log:
            action = log["action"]
            gross_value = abs(float(log["size"])) * float(log["price"])
            if action == "buy":
                campaign_cost += gross_value + float(log["commission"])
            elif action in {"sell", "sell_terminal_liquidation"} and campaign_cost > 0:
                proceeds = gross_value - float(log["commission"])
                closed_trade_returns.append((proceeds - campaign_cost) / campaign_cost)
                campaign_cost = 0.0
        total_trades = len(closed_trade_returns)
        won_trades = sum(1 for value in closed_trade_returns if value > 0)
        win_rate = (won_trades / total_trades * 100.0) if total_trades > 0 else 0.0
        total_cost_paid = sum(float(log["commission"]) for log in execution_log)
        turnover_twd = sum(abs(float(log["size"])) * float(log["price"]) for log in execution_log)

        return {
            "mode": "historical_backtest_only",
            "execution_capability": "simulated_orders_only",
            "model_version": "1.x",
            "legacy": True,
            "official_affiliation": False,
            "rule_ids": ["MA-03", "ENT-01", "ENT-03"],
            "implementation_mode": "legacy_experimental",
            "initial_cash": self.initial_cash,
            "final_value": round(final_value, 2),
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 2),
            "sharpe_ratio": round(calculated_sharpe, 2) if calculated_sharpe is not None else None,
            "commission_and_tax_paid": round(total_cost_paid, 2),
            "turnover_twd": round(turnover_twd, 2),
            "turnover_ratio": round(turnover_twd / self.initial_cash, 4),
            "exposure_pct": round(strat.position_days / strat.bars_evaluated * 100.0, 2) if strat.bars_evaluated else 0.0,
            "average_capital_utilization_pct": round(
                sum(
                    float(item.get("gross_exposure_pct", 0.0))
                    for item in strat.equity_curve
                ) / len(strat.equity_curve),
                2,
            ) if strat.equity_curve else 0.0,
            "observation_days": strat.bars_evaluated,
            "trade_start_date": trade_start_date,
            "terminal_liquidation_applied": terminal_liquidation_cost > 0,
            "cost_model": {
                "commission_rate": self.commission_rate,
                "sales_tax_rate": self.sales_tax_rate,
                "slippage_rate": self.slippage_rate,
                "lot_size": self.lot_size,
            },
            "strategy_params": {
                "stage1_ratio": self.strategy_params.get("stage1_ratio", 0.20),
                "stage2_ratio": self.strategy_params.get("stage2_ratio", 0.50),
                "stage3_ratio": self.strategy_params.get("stage3_ratio", 1.00),
                "pullback_min_pct": self.strategy_params.get("pullback_min_pct", 0.07),
                "pullback_max_pct": self.strategy_params.get("pullback_max_pct", 0.11),
            },
            "execution_log": execution_log,
            "equity_curve": strat.equity_curve,
        }
    MODEL_CLASSIFICATION = {
        "model_version": "1.x",
        "legacy": True,
        "official_affiliation": False,
        "rules": {
            "ENT-03": {"evidence_level": "U", "implementation_mode": "legacy_experimental"},
            "ENT-01": {"evidence_level": "U", "implementation_mode": "legacy_experimental"},
            "MA-03": {"evidence_level": "U", "implementation_mode": "legacy_experimental"},
        },
    }
