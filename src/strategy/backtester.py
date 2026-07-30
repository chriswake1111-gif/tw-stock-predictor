import os
import logging
import pandas as pd
import numpy as np
import backtrader as bt
from datetime import datetime
from typing import Dict, Any

from src.engine.ma_deduction import MADeductionEngine
from src.engine.wave_fibonacci import WaveFibonacciEngine
from src.strategy.capital_allocation import CapitalAllocator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class TWSalesTaxCommissionScheme(bt.CommInfoBase):
    """
    台股真實交易手續費與證交稅計算模型：
    1. 買進與賣出均扣券商手續費 (0.1425% * 60% 打折)
    2. 賣出時額外扣取證交稅 0.3% (0.003)
    """
    params = (
        ('stocklike', True),
        ('commtype', bt.CommInfoBase.COMM_PERC),
        ('commission', 0.001425 * 0.6), # 手續費 0.1425% 打 6 折
        ('tax', 0.003),                  # 賣出證交稅 0.3%
        ('discount', 0.6),
    )

    def _getcommission(self, size, price, pseudoexec):
        amount = abs(size) * price
        comm = amount * self.p.commission
        if size < 0: # 賣出時加算證交稅
            comm += amount * self.p.tax
        return comm


class TuStrategy(bt.Strategy):
    """杜金龍波浪與扣抵共振事件驅動回測策略"""

    params = (
        ('config_path', 'config/config.yaml'),
        ('symbol', '2330.TW'),
    )

    def __init__(self):
        self.dataclose = self.datas[0].close
        self.dataopen = self.datas[0].open
        self.datavol = self.datas[0].volume
        
        self.ma_engine = MADeductionEngine(config_path=self.p.config_path)
        self.allocator = CapitalAllocator(total_cash=self.broker.getvalue(), config_path=self.p.config_path)
        
        # 建立均線指標
        self.sma8 = bt.indicators.SMA(self.datas[0], period=8)
        self.sma13 = bt.indicators.SMA(self.datas[0], period=13)
        self.sma21 = bt.indicators.SMA(self.datas[0], period=21)
        self.sma55 = bt.indicators.SMA(self.datas[0], period=55)
        self.sma144 = bt.indicators.SMA(self.datas[0], period=144)

        self.stage = 0 # 建倉階段 (0: 未進場, 1: 已建 20%, 2: 已建 50%, 3: 滿載 100%)

    def next(self):
        # 至少需滿 144 日 K 線算均線
        if len(self) < 144:
            return

        close_val = self.dataclose[0]
        open_val = self.dataopen[0]
        prev_close = self.dataclose[-1]

        # 1. 均線扣抵斜率預判 (向量比對: close > shift(N-1))
        deduct_8_up = close_val > self.dataclose[-7]
        deduct_13_up = close_val > self.dataclose[-12]
        deduct_21_up = close_val > self.dataclose[-20]
        deduct_55_up = close_val > self.dataclose[-54]
        deduct_144_up = close_val > self.dataclose[-143]

        # 短天期與中長天期扣抵條件
        short_deduct_up = deduct_8_up and deduct_13_up and deduct_21_up
        bullish_alignment = (self.sma8[0] > self.sma13[0] > self.sma21[0])

        is_resonance = short_deduct_up and bullish_alignment

        # 2. 跳空開盤防禦檢查
        safe_open = self.allocator.check_gap_down_defense(open_price=open_val, prev_close=prev_close)

        # 3. 策略買賣進場邏輯
        current_cash = self.broker.getcash()
        is_index = ("^" in self.p.symbol or "0000" in self.p.symbol)
        lot_size = 1 if is_index else 1000

        # [Stage 1: 首批 20% 多空共振建倉]
        if self.stage == 0 and is_resonance and safe_open:
            size_shares = self.allocator.calculate_position_size(price=close_val, target_ratio=0.20, current_cash=current_cash, lot_size=lot_size)
            if size_shares > 0:
                self.buy(size=size_shares)
                self.stage = 1

        # [Stage 2: 浪 3 拉回 7%~11% 加碼 30%]
        elif self.stage == 1:
            highest_since_entry = max(self.dataclose.get(size=20))
            pullback_pct = (highest_since_entry - close_val) / highest_since_entry
            if 0.05 <= pullback_pct <= 0.12 and close_val > self.sma55[0]:
                size_shares = self.allocator.calculate_position_size(price=close_val, target_ratio=0.30, current_cash=current_cash, lot_size=lot_size)
                if size_shares > 0:
                    self.buy(size=size_shares)
                    self.stage = 2

        # [出場 / 風控平倉訊號: 當均線跌破 SMA55 或是短均下彎]
        elif self.position.size > 0:
            if close_val < self.sma55[0] or self.sma8[0] < self.sma21[0]:
                self.close()
                self.stage = 0


class TuBacktester:
    """Backtrader 事件驅動歷史回測引擎與戰績評估器"""

    def __init__(self, initial_cash: float = 1000000.0, config_path: str = "config/config.yaml"):
        self.initial_cash = initial_cash
        self.config_path = config_path

    def run_backtest(self, df: pd.DataFrame, symbol: str = "^TWII") -> Dict[str, Any]:
        """
        執行 Backtrader 事件驅動歷史回測
        :param df: K 線 DataFrame (需包含 date, open, high, low, close, volume)
        :param symbol: 股票代號
        """
        if df.empty:
            return {"error": "Empty dataframe"}

        # 過濾包含 NaN 的收盤價/開盤價 (防止未收盤即時 K 線干擾 Backtrader 算數)
        clean_df = df.dropna(subset=['open', 'high', 'low', 'close']).copy()

        if len(clean_df) < 144:
            logger.warning("回測資料筆數不足 (最少需 144 筆 K 線資料以計算長天期均線)")
            return {
                "symbol": symbol,
                "initial_cash": self.initial_cash,
                "final_value": self.initial_cash,
                "total_return_pct": 0.0,
                "win_rate_pct": 0.0,
                "total_trades": 0,
                "won_trades": 0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
                "note": "Insufficient data (< 144 bars)"
            }

        cerebro = bt.Cerebro()

        # 1. 將 Pandas DataFrame 轉換為 Backtrader DataFeed
        df_bt = clean_df.copy()
        df_bt['date'] = pd.to_datetime(df_bt['date'])
        df_bt = df_bt.set_index('date')

        data = bt.feeds.PandasData(
            dataname=df_bt,
            open='open',
            high='high',
            low='low',
            close='close',
            volume='volume',
            openinterest=-1
        )
        cerebro.adddata(data)

        # 2. 設置初始資金與台股真實手續費/證交稅模型
        cerebro.broker.setcash(self.initial_cash)
        comminfo = TWSalesTaxCommissionScheme()
        cerebro.broker.addcommissioninfo(comminfo)

        # 3. 掛載策略與分析器 (Analyzers)
        cerebro.addstrategy(TuStrategy, config_path=self.config_path, symbol=symbol)
        
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trade_analyzer")
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.01)

        # 4. 執行回測
        results = cerebro.run()
        strat = results[0]

        # 5. 統計與解析回測績效指標
        final_value = cerebro.broker.getvalue()
        total_return_pct = round(((final_value - self.initial_cash) / self.initial_cash) * 100, 2)

        trade_analysis = strat.analyzers.trade_analyzer.get_analysis()
        dd_analysis = strat.analyzers.drawdown.get_analysis()
        sharpe_analysis = strat.analyzers.sharpe.get_analysis()

        # 勝率計算
        total_trades = trade_analysis.get("total", {}).get("closed", 0)
        won_trades = trade_analysis.get("won", {}).get("total", 0)
        win_rate_pct = round((won_trades / total_trades * 100), 2) if total_trades > 0 else 0.0

        # 最大回撤 (MDD)
        max_drawdown_pct = round(dd_analysis.get("max", {}).get("drawdown", 0.0), 2)
        
        # 夏普比率
        sharpe_ratio = round(sharpe_analysis.get("sharperatio", 0.0) or 0.0, 2)

        logger.info(f"[{symbol}] 回測完成! 總報酬率: {total_return_pct}% | 勝率: {win_rate_pct}% | MDD: {max_drawdown_pct}% | 夏普比率: {sharpe_ratio}")

        return {
            "symbol": symbol,
            "initial_cash": self.initial_cash,
            "final_value": round(final_value, 2),
            "total_return_pct": total_return_pct,
            "win_rate_pct": win_rate_pct,
            "total_trades": total_trades,
            "won_trades": won_trades,
            "max_drawdown_pct": max_drawdown_pct,
            "sharpe_ratio": sharpe_ratio
        }
