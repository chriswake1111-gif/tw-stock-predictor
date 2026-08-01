"""離線資料快照、基準策略與 walk-forward 歷史驗證。"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.strategy.backtester import TuBacktester


DEFAULT_SYMBOLS = ["^TWII", "0050.TW", "2330.TW"]
REQUIRED_OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
DEFAULT_SENSITIVITY_CANDIDATES = [
    {
        "candidate_id": "default",
        "strategy_params": {
            "stage1_ratio": 0.20,
            "pullback_min_pct": 0.07,
            "pullback_max_pct": 0.11,
        },
    },
    {
        "candidate_id": "entry_15pct",
        "strategy_params": {
            "stage1_ratio": 0.15,
            "pullback_min_pct": 0.07,
            "pullback_max_pct": 0.11,
        },
    },
    {
        "candidate_id": "entry_25pct",
        "strategy_params": {
            "stage1_ratio": 0.25,
            "pullback_min_pct": 0.07,
            "pullback_max_pct": 0.11,
        },
    },
    {
        "candidate_id": "pullback_5_9pct",
        "strategy_params": {
            "stage1_ratio": 0.20,
            "pullback_min_pct": 0.05,
            "pullback_max_pct": 0.09,
        },
    },
    {
        "candidate_id": "pullback_9_13pct",
        "strategy_params": {
            "stage1_ratio": 0.20,
            "pullback_min_pct": 0.09,
            "pullback_max_pct": 0.13,
        },
    },
]
ADAPTIVE_ALLOCATION_PROFILES = [
    {
        "profile_id": "legacy",
        "strategy_params": {
            "stage1_ratio": 0.20,
            "stage2_ratio": 0.50,
            "stage3_ratio": 1.00,
        },
    },
    {
        "profile_id": "balanced",
        "strategy_params": {
            "stage1_ratio": 0.35,
            "stage2_ratio": 0.65,
            "stage3_ratio": 1.00,
        },
    },
]
MODEL_NAMES = ["tu_strategy", "adaptive_tu_strategy", "buy_and_hold", "sma_8_21"]
ADAPTIVE_PROFILE_VERSION = "v1.1_balanced_cap"


@dataclass(frozen=True)
class CostModel:
    commission_rate: float = 0.001425
    sales_tax_rate: float = 0.003
    slippage_rate: float = 0.001
    lot_size: int = 1000
    instrument_type: str = "stock"


def normalize_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean in {"0000", "TAIEX"}:
        return "^TWII"
    if clean.startswith("^") or clean.endswith(".TW") or clean.endswith(".TWO"):
        return clean
    return f"{clean}.TW"


def cost_model_for_symbol(symbol: str) -> CostModel:
    """股票賣出稅率 0.3%；ETF／指數代理採基金類 0.1% 研究假設。"""
    normalized = normalize_symbol(symbol)
    code = normalized.split(".")[0]
    if normalized.startswith("^"):
        return CostModel(
            sales_tax_rate=0.001,
            lot_size=1,
            instrument_type="index_etf_proxy",
        )
    if code.startswith("00"):
        return CostModel(sales_tax_rate=0.001, instrument_type="equity_etf")
    return CostModel()


def load_cached_ohlcv(
    db_path: str,
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """只讀取本地 SQLite 快取，避免研究結果受執行當下網路狀態影響。"""
    normalized = normalize_symbol(symbol)
    clauses = ["symbol = ?"]
    params: List[Any] = [normalized]
    if start_date:
        clauses.append("date >= ?")
        params.append(str(start_date))
    if end_date:
        clauses.append("date <= ?")
        params.append(str(end_date))

    query = f"""
        SELECT symbol, date, open, high, low, close, volume
        FROM daily_ohlcv
        WHERE {' AND '.join(clauses)}
        ORDER BY date ASC
    """
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(query, conn, params=params)

    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def assess_data_quality(
    df: pd.DataFrame,
    requested_symbol: str,
    reference_trading_days: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    if df.empty:
        return {
            "status": "insufficient_data",
            "symbol": normalize_symbol(requested_symbol),
            "row_count": 0,
            "reason": "No cached OHLCV records",
        }

    duplicate_dates = int(df.duplicated(subset=["date"]).sum())
    missing_by_column = {
        column: int(df[column].isna().sum())
        for column in REQUIRED_OHLCV_COLUMNS
        if column in df.columns
    }
    invalid_price_rows = int((
        (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (df["high"] < df["low"])
    ).sum())
    zero_volume_rows = int((pd.to_numeric(df["volume"], errors="coerce") <= 0).sum())
    zero_volume_dates = df.loc[
        pd.to_numeric(df["volume"], errors="coerce") <= 0, "date"
    ].astype(str).tolist()
    non_monotonic_dates = not df["date"].is_monotonic_increasing
    close_returns = df["close"].pct_change(fill_method=None).replace(
        [np.inf, -np.inf], np.nan
    )
    large_return_rows = int((close_returns.abs() >= 0.25).sum())
    max_abs_daily_return_pct = (
        round(float(close_returns.abs().max() * 100.0), 4)
        if close_returns.notna().any()
        else None
    )
    date_gaps = pd.to_datetime(df["date"], errors="coerce").diff().dt.days
    max_calendar_gap_days = int(date_gaps.max()) if date_gaps.notna().any() else 0
    missing_reference_dates: List[str] = []
    if reference_trading_days is not None:
        observed = set(df["date"].astype(str))
        start_date = str(df["date"].min())
        end_date = str(df["date"].max())
        missing_reference_dates = sorted(
            str(date)
            for date in reference_trading_days
            if start_date <= str(date) <= end_date and str(date) not in observed
        )

    status = "available"
    if (
        duplicate_dates
        or invalid_price_rows
        or large_return_rows
        or zero_volume_rows
        or missing_reference_dates
        or any(missing_by_column.values())
    ):
        status = "quality_warning"

    return {
        "status": status,
        "symbol": normalize_symbol(requested_symbol),
        "row_count": int(len(df)),
        "start_date": str(df["date"].min()),
        "end_date": str(df["date"].max()),
        "duplicate_dates": duplicate_dates,
        "missing_by_column": missing_by_column,
        "invalid_price_rows": invalid_price_rows,
        "zero_volume_rows": zero_volume_rows,
        "zero_volume_dates": zero_volume_dates,
        "missing_reference_date_count": len(missing_reference_dates),
        "missing_reference_dates": missing_reference_dates,
        "non_monotonic_dates": bool(non_monotonic_dates),
        "large_return_rows_25pct": large_return_rows,
        "max_abs_daily_return_pct": max_abs_daily_return_pct,
        "max_calendar_gap_days": max_calendar_gap_days,
        "price_adjustment_contract": "legacy_cache_unknown; new yfinance fetches explicitly use auto_adjust=True",
    }


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    clean = df.dropna(subset=REQUIRED_OHLCV_COLUMNS).copy()
    clean = clean[(clean[["open", "high", "low", "close"]] > 0).all(axis=1)]
    clean = clean[clean["high"] >= clean["low"]]
    clean = clean[pd.to_numeric(clean["volume"], errors="coerce") > 0]
    clean.sort_values("date", inplace=True)
    clean.drop_duplicates(subset=["date"], keep="last", inplace=True)
    clean.reset_index(drop=True, inplace=True)
    return clean


def dataframe_sha256(df: pd.DataFrame) -> str:
    canonical = df.to_csv(
        index=False,
        columns=[column for column in ["symbol", *REQUIRED_OHLCV_COLUMNS] if column in df.columns],
        float_format="%.10g",
        lineterminator="\n",
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_market_regime(
    benchmark_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    threshold: float = 0.15,
) -> Dict[str, Any]:
    """依驗證窗內大盤實現報酬作事後分層，不作為交易訊號。"""
    clean = clean_ohlcv(benchmark_df)
    if clean.empty or "date" not in clean.columns:
        return {
            "label": "unknown",
            "status": "insufficient_data",
            "classification_usage": "post_hoc_reporting_only",
        }
    period = clean[(clean["date"] >= start_date) & (clean["date"] <= end_date)]
    if len(period) < 2:
        return {
            "label": "unknown",
            "status": "insufficient_data",
            "classification_usage": "post_hoc_reporting_only",
        }
    realized_return = float(period.iloc[-1]["close"] / period.iloc[0]["close"] - 1.0)
    if realized_return >= threshold:
        label = "bull"
    elif realized_return <= -threshold:
        label = "bear"
    else:
        label = "sideways"
    return {
        "label": label,
        "status": "available",
        "benchmark_return_pct": round(realized_return * 100.0, 4),
        "threshold_pct": round(threshold * 100.0, 2),
        "benchmark_rows": int(len(period)),
        "classification_usage": "post_hoc_reporting_only",
    }


def _max_drawdown_pct(equity: Iterable[float]) -> float:
    values = pd.Series(list(equity), dtype=float)
    if values.empty:
        return 0.0
    peaks = values.cummax()
    drawdowns = (values / peaks - 1.0) * 100.0
    return round(abs(float(drawdowns.min())), 4)


def _annualized_sharpe(equity: Iterable[float]) -> Optional[float]:
    values = pd.Series(list(equity), dtype=float)
    returns = values.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if len(returns) < 2 or float(returns.std(ddof=1)) == 0.0:
        return None
    return round(float(returns.mean() / returns.std(ddof=1) * math.sqrt(252)), 4)


def _finalize_baseline_metrics(
    name: str,
    initial_cash: float,
    final_value: float,
    equity_curve: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    commission_paid: float,
    turnover_twd: float,
    position_days: int,
    observation_days: int,
    closed_trade_returns: List[float],
) -> Dict[str, Any]:
    total_return_pct = (final_value / initial_cash - 1.0) * 100.0
    won = sum(1 for value in closed_trade_returns if value > 0)
    return {
        "model": name,
        "mode": "historical_backtest_only",
        "execution_capability": "simulated_orders_only",
        "initial_cash": round(initial_cash, 2),
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return_pct, 4),
        "max_drawdown_pct": _max_drawdown_pct(item["equity"] for item in equity_curve),
        "sharpe_ratio": _annualized_sharpe(item["equity"] for item in equity_curve),
        "total_trades": len(closed_trade_returns),
        "win_rate_pct": round(won / len(closed_trade_returns) * 100.0, 2) if closed_trade_returns else 0.0,
        "commission_and_tax_paid": round(commission_paid, 2),
        "turnover_twd": round(turnover_twd, 2),
        "turnover_ratio": round(turnover_twd / initial_cash, 4) if initial_cash > 0 else 0.0,
        "exposure_pct": round(position_days / observation_days * 100.0, 2) if observation_days else 0.0,
        "average_capital_utilization_pct": round(
            float(np.mean([
                item.get("gross_exposure_pct", 0.0) for item in equity_curve
            ])),
            2,
        ) if equity_curve else 0.0,
        "observation_days": observation_days,
        "trades": trades,
        "equity_curve": equity_curve,
    }


def simulate_buy_and_hold(
    validation_df: pd.DataFrame,
    initial_cash: float,
    costs: CostModel,
) -> Dict[str, Any]:
    df = clean_ohlcv(validation_df)
    if df.empty:
        return {"model": "buy_and_hold", "error": "No validation data"}

    first = df.iloc[0]
    execution_price = float(first["open"]) * (1.0 + costs.slippage_rate)
    shares = math.floor(
        initial_cash / (execution_price * (1.0 + costs.commission_rate) * costs.lot_size)
    ) * costs.lot_size
    if shares <= 0:
        return {"model": "buy_and_hold", "error": "Initial cash cannot purchase one board lot"}

    buy_value = shares * execution_price
    buy_fee = buy_value * costs.commission_rate
    cash = initial_cash - buy_value - buy_fee
    trades = [{
        "signal_date": str(first["date"]),
        "execution_date": str(first["date"]),
        "action": "buy",
        "size": shares,
        "price": round(execution_price, 4),
        "commission_and_tax": round(buy_fee, 2),
    }]
    equity_curve = []
    for _, row in df.iterrows():
        equity = float(cash + shares * row["close"])
        equity_curve.append({
            "date": str(row["date"]),
            "equity": equity,
            "gross_exposure_pct": round(
                float(shares * row["close"]) / equity * 100.0, 4
            ) if equity > 0 else 0.0,
        })

    last = df.iloc[-1]
    sell_price = float(last["close"]) * (1.0 - costs.slippage_rate)
    sell_value = shares * sell_price
    sell_fee = sell_value * (costs.commission_rate + costs.sales_tax_rate)
    final_value = cash + sell_value - sell_fee
    equity_curve[-1]["equity"] = final_value
    trades.append({
        "signal_date": str(last["date"]),
        "execution_date": str(last["date"]),
        "action": "sell_terminal_liquidation",
        "size": -shares,
        "price": round(sell_price, 4),
        "commission_and_tax": round(sell_fee, 2),
    })
    return _finalize_baseline_metrics(
        "buy_and_hold",
        initial_cash,
        final_value,
        equity_curve,
        trades,
        buy_fee + sell_fee,
        buy_value + sell_value,
        len(df),
        len(df),
        [(final_value - initial_cash) / initial_cash],
    )


def simulate_sma_baseline(
    context_df: pd.DataFrame,
    validation_start: str,
    validation_end: str,
    initial_cash: float,
    costs: CostModel,
    short_period: int = 8,
    long_period: int = 21,
) -> Dict[str, Any]:
    df = clean_ohlcv(context_df)
    if df.empty:
        return {"model": "sma_8_21", "error": "No validation data"}
    df[f"sma_{short_period}"] = df["close"].rolling(short_period).mean()
    df[f"sma_{long_period}"] = df["close"].rolling(long_period).mean()

    validation_mask = (df["date"] >= validation_start) & (df["date"] <= validation_end)
    validation_indices = df.index[validation_mask].tolist()
    if not validation_indices:
        return {"model": "sma_8_21", "error": "No validation observations"}

    cash = initial_cash
    shares = 0
    entry_total_cost = 0.0
    commission_paid = 0.0
    turnover_twd = 0.0
    position_days = 0
    trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    closed_trade_returns: List[float] = []

    for index in validation_indices:
        row = df.loc[index]
        previous = df.loc[index - 1] if index > 0 else row
        bullish_signal = bool(
            pd.notna(previous[f"sma_{long_period}"])
            and previous[f"sma_{short_period}"] > previous[f"sma_{long_period}"]
        )

        if bullish_signal and shares == 0:
            execution_price = float(row["open"]) * (1.0 + costs.slippage_rate)
            affordable = math.floor(
                cash / (execution_price * (1.0 + costs.commission_rate) * costs.lot_size)
            ) * costs.lot_size
            if affordable > 0:
                buy_value = affordable * execution_price
                fee = buy_value * costs.commission_rate
                cash -= buy_value + fee
                shares = affordable
                entry_total_cost = buy_value + fee
                commission_paid += fee
                turnover_twd += buy_value
                trades.append({
                    "signal_date": str(previous["date"]),
                    "execution_date": str(row["date"]),
                    "action": "buy",
                    "size": shares,
                    "price": round(execution_price, 4),
                    "commission_and_tax": round(fee, 2),
                })
        elif not bullish_signal and shares > 0:
            execution_price = float(row["open"]) * (1.0 - costs.slippage_rate)
            sell_value = shares * execution_price
            fee = sell_value * (costs.commission_rate + costs.sales_tax_rate)
            proceeds = sell_value - fee
            cash += proceeds
            closed_trade_returns.append((proceeds - entry_total_cost) / entry_total_cost)
            commission_paid += fee
            turnover_twd += sell_value
            trades.append({
                "signal_date": str(previous["date"]),
                "execution_date": str(row["date"]),
                "action": "sell",
                "size": -shares,
                "price": round(execution_price, 4),
                "commission_and_tax": round(fee, 2),
            })
            shares = 0
            entry_total_cost = 0.0

        if shares > 0:
            position_days += 1
        equity = float(cash + shares * row["close"])
        equity_curve.append({
            "date": str(row["date"]),
            "equity": equity,
            "gross_exposure_pct": round(
                float(shares * row["close"]) / equity * 100.0, 4
            ) if equity > 0 else 0.0,
        })

    if shares > 0:
        last = df.loc[validation_indices[-1]]
        execution_price = float(last["close"]) * (1.0 - costs.slippage_rate)
        sell_value = shares * execution_price
        fee = sell_value * (costs.commission_rate + costs.sales_tax_rate)
        proceeds = sell_value - fee
        cash += proceeds
        closed_trade_returns.append((proceeds - entry_total_cost) / entry_total_cost)
        commission_paid += fee
        turnover_twd += sell_value
        trades.append({
            "signal_date": str(last["date"]),
            "execution_date": str(last["date"]),
            "action": "sell_terminal_liquidation",
            "size": -shares,
            "price": round(execution_price, 4),
            "commission_and_tax": round(fee, 2),
        })
        equity_curve[-1]["equity"] = cash

    return _finalize_baseline_metrics(
        f"sma_{short_period}_{long_period}",
        initial_cash,
        cash,
        equity_curve,
        trades,
        commission_paid,
        turnover_twd,
        position_days,
        len(validation_indices),
        closed_trade_returns,
    )


def build_walk_forward_windows(
    df: pd.DataFrame,
    requested_start: Optional[str],
    requested_end: Optional[str],
    validation_months: int = 12,
    warmup_bars: int = 233,
    min_validation_bars: int = 120,
) -> List[Dict[str, Any]]:
    clean = clean_ohlcv(df)
    if clean.empty or validation_months <= 0 or warmup_bars < 1:
        return []

    earliest_validation_index = warmup_bars
    if requested_start:
        matching = clean.index[clean["date"] >= str(requested_start)].tolist()
        if not matching:
            return []
        earliest_validation_index = max(earliest_validation_index, matching[0])
    if earliest_validation_index >= len(clean):
        return []

    final_date = pd.Timestamp(requested_end or clean.iloc[-1]["date"])
    current_start = pd.Timestamp(clean.iloc[earliest_validation_index]["date"])
    windows: List[Dict[str, Any]] = []
    sequence = 1

    while current_start <= final_date:
        next_start = current_start + pd.DateOffset(months=validation_months)
        window_end = min(next_start - pd.Timedelta(days=1), final_date)
        validation_mask = (
            (pd.to_datetime(clean["date"]) >= current_start)
            & (pd.to_datetime(clean["date"]) <= window_end)
        )
        validation_indices = clean.index[validation_mask].tolist()
        if len(validation_indices) >= min_validation_bars:
            first_index = validation_indices[0]
            last_index = validation_indices[-1]
            context_start_index = max(0, first_index - warmup_bars)
            windows.append({
                "sequence": sequence,
                "context_start_index": context_start_index,
                "validation_start_index": first_index,
                "validation_end_index": last_index,
                "context_start": str(clean.loc[context_start_index, "date"]),
                "validation_start": str(clean.loc[first_index, "date"]),
                "validation_end": str(clean.loc[last_index, "date"]),
                "warmup_bars": first_index - context_start_index,
                "validation_bars": len(validation_indices),
            })
            sequence += 1
        current_start = next_start

    return windows


def evaluate_training_parameter_sensitivity(
    df: pd.DataFrame,
    windows_spec: List[Dict[str, Any]],
    initial_cash: float,
    costs: CostModel,
    candidates: Optional[List[Dict[str, Any]]] = None,
    lookback_bars: int = 504,
    warmup_bars: int = 144,
    min_training_bars: int = 252,
) -> Dict[str, Any]:
    """只在各驗證窗之前的資料比較小幅參數變體，不將排名套回驗證窗。"""
    clean = clean_ohlcv(df)
    candidate_specs = candidates or DEFAULT_SENSITIVITY_CANDIDATES
    candidate_results: Dict[str, List[Dict[str, Any]]] = {
        item["candidate_id"]: [] for item in candidate_specs
    }
    training_windows: List[Dict[str, Any]] = []

    for spec in windows_spec:
        training_end_index = spec["validation_start_index"] - 1
        if training_end_index < 0:
            continue
        trade_start_index = max(0, training_end_index - lookback_bars + 1)
        context_start_index = max(0, trade_start_index - warmup_bars)
        training_observations = training_end_index - trade_start_index + 1
        if training_observations < min_training_bars:
            continue

        context = clean.iloc[context_start_index:training_end_index + 1].copy()
        training_start = str(clean.loc[trade_start_index, "date"])
        training_end = str(clean.loc[training_end_index, "date"])
        window_record = {
            "validation_window": spec["sequence"],
            "training_start": training_start,
            "training_end": training_end,
            "validation_start": spec["validation_start"],
            "training_observations": training_observations,
            "candidates": [],
        }
        for candidate in candidate_specs:
            result = TuBacktester(
                initial_cash=initial_cash,
                sales_tax_rate=costs.sales_tax_rate,
                commission_rate=costs.commission_rate,
                slippage_rate=costs.slippage_rate,
                lot_size=costs.lot_size,
                strategy_params=candidate["strategy_params"],
            ).run_backtest(context, trade_start_date=training_start)
            compact = {
                key: value
                for key, value in result.items()
                if key not in {"execution_log", "equity_curve"}
            }
            candidate_record = {
                "candidate_id": candidate["candidate_id"],
                "strategy_params": candidate["strategy_params"],
                **compact,
            }
            window_record["candidates"].append(candidate_record)
            if "error" not in result:
                candidate_results[candidate["candidate_id"]].append(candidate_record)
        training_windows.append(window_record)

    summaries = []
    for candidate in candidate_specs:
        values = candidate_results[candidate["candidate_id"]]
        if not values:
            summaries.append({
                "candidate_id": candidate["candidate_id"],
                "strategy_params": candidate["strategy_params"],
                "status": "insufficient_data",
                "training_window_count": 0,
            })
            continue
        returns = [float(item["total_return_pct"]) for item in values]
        summaries.append({
            "candidate_id": candidate["candidate_id"],
            "strategy_params": candidate["strategy_params"],
            "status": "available",
            "training_window_count": len(values),
            "average_return_pct": round(float(np.mean(returns)), 4),
            "median_return_pct": round(float(np.median(returns)), 4),
            "positive_window_fraction": round(
                sum(value > 0 for value in returns) / len(returns), 4
            ),
            "worst_drawdown_pct": round(
                max(float(item.get("max_drawdown_pct", 0.0)) for item in values), 4
            ),
            "total_trades": sum(int(item.get("total_trades", 0)) for item in values),
        })

    available = [item for item in summaries if item["status"] == "available"]
    average_returns = [float(item["average_return_pct"]) for item in available]
    return {
        "status": "available" if available else "insufficient_data",
        "mode": "training_only_descriptive_sensitivity",
        "selection_applied_to_validation": False,
        "lookback_bars": lookback_bars,
        "warmup_bars": warmup_bars,
        "min_training_bars": min_training_bars,
        "candidate_count": len(candidate_specs),
        "average_return_range_pct_points": round(
            max(average_returns) - min(average_returns), 4
        ) if average_returns else None,
        "positive_candidate_fraction": round(
            sum(value > 0 for value in average_returns) / len(average_returns), 4
        ) if average_returns else None,
        "candidate_summaries": summaries,
        "training_windows": training_windows,
    }


def select_adaptive_allocation_profile(
    df: pd.DataFrame,
    validation_start_index: int,
    initial_cash: float,
    costs: CostModel,
    profiles: Optional[List[Dict[str, Any]]] = None,
    lookback_bars: int = 504,
    warmup_bars: int = 144,
    min_training_bars: int = 252,
    max_training_drawdown_pct: float = 12.0,
    min_training_trades: int = 2,
) -> Dict[str, Any]:
    """以驗證窗之前的固定長度資料選擇配置 profile，訊號規則維持不變。"""
    clean = clean_ohlcv(df)
    profile_specs = profiles or ADAPTIVE_ALLOCATION_PROFILES
    legacy = next(item for item in profile_specs if item["profile_id"] == "legacy")
    training_end_index = validation_start_index - 1
    trade_start_index = max(0, training_end_index - lookback_bars + 1)
    training_observations = training_end_index - trade_start_index + 1
    context_start_index = max(0, trade_start_index - warmup_bars)

    base = {
        "mode": "training_only_profile_selection",
        "profile_version": ADAPTIVE_PROFILE_VERSION,
        "selection_applied_to_current_validation": True,
        "lookback_bars": lookback_bars,
        "max_training_drawdown_pct": max_training_drawdown_pct,
        "min_training_trades": min_training_trades,
        "validation_start": str(clean.loc[validation_start_index, "date"]),
        "candidate_results": [],
    }
    if training_end_index < 0 or training_observations < min_training_bars:
        return {
            **base,
            "status": "fallback",
            "reason": "insufficient_pre_validation_training_data",
            "selected_profile_id": "legacy",
            "selected_strategy_params": legacy["strategy_params"],
            "training_start": None,
            "training_end": None,
        }

    training_start = str(clean.loc[trade_start_index, "date"])
    training_end = str(clean.loc[training_end_index, "date"])
    context = clean.iloc[context_start_index:training_end_index + 1].copy()
    candidates = []
    for profile in profile_specs:
        result = TuBacktester(
            initial_cash=initial_cash,
            sales_tax_rate=costs.sales_tax_rate,
            commission_rate=costs.commission_rate,
            slippage_rate=costs.slippage_rate,
            lot_size=costs.lot_size,
            strategy_params=profile["strategy_params"],
        ).run_backtest(context, trade_start_date=training_start)
        eligible = (
            "error" not in result
            and float(result.get("total_return_pct", 0.0)) > 0.0
            and float(result.get("max_drawdown_pct", float("inf")))
            <= max_training_drawdown_pct
            and int(result.get("total_trades", 0)) >= min_training_trades
        )
        candidates.append({
            "profile_id": profile["profile_id"],
            "strategy_params": profile["strategy_params"],
            "eligible": eligible,
            "total_return_pct": result.get("total_return_pct"),
            "max_drawdown_pct": result.get("max_drawdown_pct"),
            "total_trades": result.get("total_trades", 0),
            "average_capital_utilization_pct": result.get(
                "average_capital_utilization_pct", 0.0
            ),
        })

    eligible_candidates = [item for item in candidates if item["eligible"]]
    if eligible_candidates:
        selected = max(
            eligible_candidates,
            key=lambda item: (
                float(item["total_return_pct"]),
                -float(item["max_drawdown_pct"]),
            ),
        )
        status = "selected"
        reason = "highest_positive_training_return_within_risk_guardrails"
    else:
        selected = next(item for item in candidates if item["profile_id"] == "legacy")
        status = "fallback"
        reason = "no_profile_passed_training_risk_guardrails"

    return {
        **base,
        "status": status,
        "reason": reason,
        "training_start": training_start,
        "training_end": training_end,
        "training_observations": training_observations,
        "selected_profile_id": selected["profile_id"],
        "selected_strategy_params": selected["strategy_params"],
        "candidate_results": candidates,
    }


def _aggregate_windows(windows: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    results = [window["models"].get(model, {}) for window in windows]
    valid = [result for result in results if "error" not in result]
    if not valid:
        return {"model": model, "status": "insufficient_data"}

    compounded = 1.0
    for result in valid:
        compounded *= 1.0 + float(result["total_return_pct"]) / 100.0
    observations = sum(int(result.get("observation_days", 0)) for result in valid)
    exposure_weighted = sum(
        float(result.get("exposure_pct", 0.0)) * int(result.get("observation_days", 0))
        for result in valid
    )
    capital_utilization_weighted = sum(
        float(result.get("average_capital_utilization_pct", 0.0))
        * int(result.get("observation_days", 0))
        for result in valid
    )
    sharpe_values = [
        float(result["sharpe_ratio"])
        for result in valid
        if result.get("sharpe_ratio") is not None
    ]
    total_trades = sum(int(result.get("total_trades", 0)) for result in valid)
    won_estimate = sum(
        int(round(result.get("total_trades", 0) * result.get("win_rate_pct", 0.0) / 100.0))
        for result in valid
    )

    return {
        "model": model,
        "status": "available" if len(valid) == len(windows) else "partial",
        "window_count": len(valid),
        "requested_window_count": len(windows),
        "compounded_return_pct": round((compounded - 1.0) * 100.0, 4),
        "worst_window_drawdown_pct": round(max(float(result.get("max_drawdown_pct", 0.0)) for result in valid), 4),
        "average_window_sharpe": round(float(np.mean(sharpe_values)), 4) if sharpe_values else None,
        "total_trades": total_trades,
        "win_rate_pct": round(won_estimate / total_trades * 100.0, 2) if total_trades else 0.0,
        "commission_and_tax_paid": round(sum(float(result.get("commission_and_tax_paid", 0.0)) for result in valid), 2),
        "turnover_twd": round(sum(float(result.get("turnover_twd", 0.0)) for result in valid), 2),
        "exposure_pct": round(exposure_weighted / observations, 2) if observations else 0.0,
        "average_capital_utilization_pct": round(
            capital_utilization_weighted / observations, 2
        ) if observations else 0.0,
        "observation_days": observations,
    }


def evaluate_symbol_walk_forward(
    symbol: str,
    df: pd.DataFrame,
    initial_cash: float = 1_000_000.0,
    requested_start: Optional[str] = None,
    requested_end: Optional[str] = None,
    validation_months: int = 12,
    warmup_bars: int = 233,
    min_validation_bars: int = 120,
    costs: Optional[CostModel] = None,
    benchmark_df: Optional[pd.DataFrame] = None,
    data_provenance: Optional[Dict[str, Any]] = None,
    sensitivity_enabled: bool = True,
) -> Dict[str, Any]:
    costs = costs or cost_model_for_symbol(symbol)
    reference_trading_days = None
    if benchmark_df is not None and not benchmark_df.empty and "date" in benchmark_df:
        reference_trading_days = benchmark_df["date"].astype(str).tolist()
    quality = assess_data_quality(df, symbol, reference_trading_days)
    adjustment_contract = (data_provenance or {}).get("adjustment_contract")

    def provenance_dates(key: str) -> set[str]:
        value = (data_provenance or {}).get(key)
        if isinstance(value, str):
            try:
                return {str(item) for item in json.loads(value)}
            except (json.JSONDecodeError, TypeError):
                return set()
        if isinstance(value, list):
            return {str(item) for item in value}
        return set()

    requested_exempt_dates = provenance_dates("official_reference_exempt_dates")
    exemption_evidence_dates = provenance_dates(
        "official_explained_no_bar_dates"
    ).union(provenance_dates("benchmark_provider_non_market_dates"))
    exemption_contract_valid = (
        adjustment_contract == "provider_compatible_adjusted_v1"
        and requested_exempt_dates.issubset(exemption_evidence_dates)
    )
    exempt_dates = requested_exempt_dates if exemption_contract_valid else set()
    if requested_exempt_dates:
        quality["official_reference_exemption_contract_valid"] = (
            exemption_contract_valid
        )
        if not exemption_contract_valid:
            quality["status"] = "quality_warning"
    if exempt_dates and quality.get("missing_reference_dates"):
        original_missing = quality["missing_reference_dates"]
        remaining_missing = [
            date for date in original_missing if date not in exempt_dates
        ]
        quality["missing_reference_dates"] = remaining_missing
        quality["missing_reference_date_count"] = len(remaining_missing)
        quality["official_reference_exempt_dates_applied"] = sorted(
            set(original_missing) - set(remaining_missing)
        )
        base_warning = (
            quality.get("duplicate_dates", 0)
            or quality.get("invalid_price_rows", 0)
            or quality.get("large_return_rows_25pct", 0)
            or quality.get("zero_volume_rows", 0)
            or remaining_missing
            or any(quality.get("missing_by_column", {}).values())
        )
        quality["status"] = "quality_warning" if base_warning else "available"
    removed_zero_volume_rows = int(
        (data_provenance or {}).get("zero_volume_rows_removed", 0)
    )
    if removed_zero_volume_rows:
        quality["zero_volume_rows_removed_before_evaluation"] = removed_zero_volume_rows
        quality["zero_volume_dates_removed_before_evaluation"] = (
            data_provenance or {}
        ).get("zero_volume_dates_removed", [])
        if (data_provenance or {}).get("zero_volume_reconciliation_status") != "available":
            quality["status"] = "quality_warning"
    official_integrity_status = (data_provenance or {}).get(
        "official_integrity_status"
    )
    if official_integrity_status:
        quality["official_integrity_status"] = official_integrity_status
        quality["official_gap_rows_inserted"] = int(
            (data_provenance or {}).get("official_gap_rows_inserted", 0)
        )
        quality["official_gap_rows_unresolved"] = int(
            (data_provenance or {}).get("official_gap_rows_unresolved", 0)
        )
        if official_integrity_status != "available":
            quality["status"] = "quality_warning"
    if adjustment_contract == "provider_compatible_adjusted_v1":
        quality["price_adjustment_contract"] = adjustment_contract
    elif data_provenance and data_provenance.get("auto_adjust") is True:
        quality["price_adjustment_contract"] = (
            "yfinance auto_adjust=True; actions=False; repair=False"
        )
    clean = clean_ohlcv(df)
    dataset_hash = dataframe_sha256(clean) if not clean.empty else None
    windows_spec = build_walk_forward_windows(
        clean,
        requested_start=requested_start,
        requested_end=requested_end,
        validation_months=validation_months,
        warmup_bars=warmup_bars,
        min_validation_bars=min_validation_bars,
    )
    windows: List[Dict[str, Any]] = []
    benchmark = benchmark_df if benchmark_df is not None else clean

    for spec in windows_spec:
        context = clean.iloc[
            spec["context_start_index"]:spec["validation_end_index"] + 1
        ].copy()
        validation = clean.iloc[
            spec["validation_start_index"]:spec["validation_end_index"] + 1
        ].copy()
        strategy = TuBacktester(
            initial_cash=initial_cash,
            sales_tax_rate=costs.sales_tax_rate,
            commission_rate=costs.commission_rate,
            slippage_rate=costs.slippage_rate,
            lot_size=costs.lot_size,
        ).run_backtest(
            context,
            trade_start_date=spec["validation_start"],
        )
        adaptive_selection = select_adaptive_allocation_profile(
            clean,
            validation_start_index=spec["validation_start_index"],
            initial_cash=initial_cash,
            costs=costs,
        )
        adaptive_strategy = TuBacktester(
            initial_cash=initial_cash,
            sales_tax_rate=costs.sales_tax_rate,
            commission_rate=costs.commission_rate,
            slippage_rate=costs.slippage_rate,
            lot_size=costs.lot_size,
            strategy_params=adaptive_selection["selected_strategy_params"],
        ).run_backtest(
            context,
            trade_start_date=spec["validation_start"],
        )
        adaptive_strategy["adaptive_selection"] = {
            key: value
            for key, value in adaptive_selection.items()
            if key != "candidate_results"
        }
        buy_hold = simulate_buy_and_hold(validation, initial_cash, costs)
        sma = simulate_sma_baseline(
            context,
            validation_start=spec["validation_start"],
            validation_end=spec["validation_end"],
            initial_cash=initial_cash,
            costs=costs,
        )
        minimum_price = float(validation["close"].min())
        stage1_minimum_cash = (
            minimum_price * costs.lot_size * (1.0 + costs.commission_rate) / 0.20
        )
        windows.append({
            **{key: value for key, value in spec.items() if not key.endswith("_index")},
            "capital_feasibility": {
                "lot_size": costs.lot_size,
                "minimum_close_in_window": round(minimum_price, 4),
                "estimated_minimum_cash_for_stage1": round(stage1_minimum_cash, 2),
                "stage1_board_lot_feasible": initial_cash >= stage1_minimum_cash,
            },
            "market_regime": classify_market_regime(
                benchmark,
                spec["validation_start"],
                spec["validation_end"],
            ),
            "adaptive_profile_selection": adaptive_selection,
            "models": {
                "tu_strategy": strategy,
                "adaptive_tu_strategy": adaptive_strategy,
                "buy_and_hold": buy_hold,
                "sma_8_21": sma,
            },
        })

    aggregates = {
        model: _aggregate_windows(windows, model)
        for model in MODEL_NAMES
    }
    regime_aggregates: Dict[str, Dict[str, Any]] = {}
    for regime in ["bull", "bear", "sideways", "unknown"]:
        matching_windows = [
            window
            for window in windows
            if window["market_regime"]["label"] == regime
        ]
        if matching_windows:
            regime_aggregates[regime] = {
                model: _aggregate_windows(matching_windows, model)
                for model in MODEL_NAMES
            }
    if all(
        aggregates[model].get("status") == "available"
        for model in MODEL_NAMES
    ):
        strategy_return = aggregates["tu_strategy"]["compounded_return_pct"]
        aggregates["tu_strategy"]["excess_vs_buy_hold_pct"] = round(
            strategy_return - aggregates["buy_and_hold"]["compounded_return_pct"], 4
        )
        aggregates["tu_strategy"]["excess_vs_sma_pct"] = round(
            strategy_return - aggregates["sma_8_21"]["compounded_return_pct"], 4
        )
        adaptive_return = aggregates["adaptive_tu_strategy"]["compounded_return_pct"]
        aggregates["adaptive_tu_strategy"]["excess_vs_legacy_pct"] = round(
            adaptive_return - strategy_return, 4
        )
        aggregates["adaptive_tu_strategy"]["excess_vs_buy_hold_pct"] = round(
            adaptive_return - aggregates["buy_and_hold"]["compounded_return_pct"], 4
        )
        aggregates["adaptive_tu_strategy"]["excess_vs_sma_pct"] = round(
            adaptive_return - aggregates["sma_8_21"]["compounded_return_pct"], 4
        )

    adaptive_aggregate = aggregates.get("adaptive_tu_strategy", {})
    legacy_aggregate = aggregates.get("tu_strategy", {})
    bear_adaptive = regime_aggregates.get("bear", {}).get(
        "adaptive_tu_strategy", {}
    )
    adaptive_gate_checks = {
        "data_quality_available": quality.get("status") == "available",
        "return_improves_legacy": (
            adaptive_aggregate.get("status") == "available"
            and legacy_aggregate.get("status") == "available"
            and float(adaptive_aggregate.get("compounded_return_pct", 0.0))
            > float(legacy_aggregate.get("compounded_return_pct", 0.0))
        ),
        "worst_window_mdd_at_most_12pct": (
            adaptive_aggregate.get("status") == "available"
            and float(adaptive_aggregate.get("worst_window_drawdown_pct", float("inf")))
            <= 12.0
        ),
        "bear_window_present": bear_adaptive.get("status") == "available",
        "bear_compounded_return_above_minus_5pct": (
            bear_adaptive.get("status") == "available"
            and float(bear_adaptive.get("compounded_return_pct", -float("inf")))
            > -5.0
        ),
        "bear_worst_mdd_at_most_5pct": (
            bear_adaptive.get("status") == "available"
            and float(bear_adaptive.get("worst_window_drawdown_pct", float("inf")))
            <= 5.0
        ),
    }
    adaptive_gate_passed = all(adaptive_gate_checks.values())

    return {
        "symbol": normalize_symbol(symbol),
        "status": "available" if windows else "insufficient_data",
        "mode": "walk_forward_historical_research",
        "optimization_performed": False,
        "adaptive_profile_selection_performed": bool(windows),
        "validation_data_used_for_profile_selection": False,
        "dataset_sha256": dataset_hash,
        "data_provenance": data_provenance or {
            "provider": "local_sqlite_cache",
            "official_exchange_source": False,
            "adjustment_contract": "legacy_cache_unknown",
        },
        "data_quality": quality,
        "cost_model": asdict(costs),
        "configuration": {
            "initial_cash": initial_cash,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "validation_months": validation_months,
            "warmup_bars": warmup_bars,
            "min_validation_bars": min_validation_bars,
            "adaptive_profile_selection": {
                "profile_version": ADAPTIVE_PROFILE_VERSION,
                "profiles": ADAPTIVE_ALLOCATION_PROFILES,
                "lookback_bars": 504,
                "warmup_bars": 144,
                "min_training_bars": 252,
                "max_training_drawdown_pct": 12.0,
                "min_training_trades": 2,
                "fallback_profile": "legacy",
            },
        },
        "aggregates": aggregates,
        "regime_aggregates": regime_aggregates,
        "adaptive_research_gate": {
            "status": (
                "candidate_for_broader_validation"
                if adaptive_gate_passed
                else "hold_for_revision"
            ),
            "passed": adaptive_gate_passed,
            "promotion_to_default": False,
            "reason": (
                "passing this research gate is necessary but not sufficient; "
                "broader symbols and bear samples are still required"
            ),
            "checks": adaptive_gate_checks,
        },
        "parameter_sensitivity": evaluate_training_parameter_sensitivity(
            clean,
            windows_spec,
            initial_cash,
            costs,
        ) if sensitivity_enabled and windows_spec else {
            "status": "disabled",
            "selection_applied_to_validation": False,
        },
        "windows": windows,
    }


def assess_expanded_universe(
    evaluations: List[Dict[str, Any]],
    universe_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a preregistered aggregate gate without dropping failed symbols."""
    configured_symbols = universe_config.get("symbols", [])
    category_by_symbol = {
        normalize_symbol(item["symbol"]): item["category"]
        for item in configured_symbols
    }
    thresholds = universe_config["promotion_policy"]["universe_gate"]
    available = [
        item
        for item in evaluations
        if item.get("status") == "available"
        and item.get("data_quality", {}).get("status") == "available"
    ]
    passed = [
        item
        for item in available
        if item.get("adaptive_research_gate", {}).get("passed") is True
    ]
    mdds = [
        item.get("aggregates", {})
        .get("adaptive_tu_strategy", {})
        .get("worst_window_drawdown_pct")
        for item in available
    ]
    mdds = [float(value) for value in mdds if value is not None]
    usable_count = len(available)
    total_count = len(configured_symbols)
    pass_rate = len(passed) / total_count if total_count else 0.0
    maximum_mdd = max(mdds) if mdds else None

    categories: Dict[str, Dict[str, int]] = {}
    for evaluation in evaluations:
        category = category_by_symbol.get(evaluation.get("symbol"), "unclassified")
        row = categories.setdefault(category, {"total_symbols": 0, "usable_symbols": 0, "passed_symbols": 0})
        row["total_symbols"] += 1
        if (
            evaluation.get("status") == "available"
            and evaluation.get("data_quality", {}).get("status") == "available"
        ):
            row["usable_symbols"] += 1
            if evaluation.get("adaptive_research_gate", {}).get("passed") is True:
                row["passed_symbols"] += 1

    checks = {
        "minimum_usable_symbols_met": usable_count
        >= int(thresholds["minimum_usable_symbols"]),
        "minimum_gate_pass_rate_met": pass_rate
        >= float(thresholds["minimum_gate_pass_rate"]),
        "maximum_symbol_mdd_met": maximum_mdd is not None
        and maximum_mdd <= float(thresholds["maximum_symbol_mdd_pct"]),
    }
    overall_passed = all(checks.values())
    return {
        "universe_id": universe_config["universe_id"],
        "preregistration_status": universe_config["status"],
        "status": "candidate_for_phase_b" if overall_passed else "hold_for_revision",
        "passed": overall_passed,
        "promotion_to_default": False,
        "total_symbols": total_count,
        "usable_symbols": usable_count,
        "passed_symbols": len(passed),
        "gate_pass_rate": pass_rate,
        "maximum_adaptive_mdd_pct": maximum_mdd,
        "thresholds": thresholds,
        "checks": checks,
        "category_summary": [
            {"category": category, **counts}
            for category, counts in sorted(categories.items())
        ],
    }


def write_walk_forward_report(
    evaluations: List[Dict[str, Any]],
    snapshots: Dict[str, pd.DataFrame],
    output_dir: str,
    run_timestamp: Optional[str] = None,
    universe_assessment: Optional[Dict[str, Any]] = None,
) -> Path:
    timestamp = run_timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    fingerprint = [
        {
            "symbol": item.get("symbol"),
            "dataset_sha256": item.get("dataset_sha256"),
            "configuration": item.get("configuration"),
            "cost_model": item.get("cost_model"),
            "provenance_contract": {
                key: item.get("data_provenance", {}).get(key)
                for key in [
                    "provider",
                    "library_version",
                    "auto_adjust",
                    "actions",
                    "repair",
                ]
            },
        }
        for item in evaluations
    ]
    combined_hash = hashlib.sha256(
        json.dumps(
            {"evaluations": fingerprint, "universe_assessment": universe_assessment},
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    run_dir = Path(output_dir) / f"{timestamp}-{combined_hash}"
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot_dir = run_dir / "data"
    snapshot_dir.mkdir()

    for symbol, frame in snapshots.items():
        safe_symbol = symbol.replace("^", "index-").replace(".", "-")
        frame.to_csv(snapshot_dir / f"{safe_symbol}.csv", index=False, encoding="utf-8")

    report = {
        "schema_version": 2,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "walk_forward_historical_research",
        "execution_capability": "simulated_orders_only",
        "disclaimer": "僅供個人市場研究與決策參考，不構成投資建議、報酬保證或真實委託指令。",
        "evaluations": evaluations,
        "universe_assessment": universe_assessment,
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    aggregate_rows: List[Dict[str, Any]] = []
    window_rows: List[Dict[str, Any]] = []
    trade_rows: List[Dict[str, Any]] = []
    quality_rows: List[Dict[str, Any]] = []
    regime_rows: List[Dict[str, Any]] = []
    sensitivity_rows: List[Dict[str, Any]] = []
    provenance_rows: List[Dict[str, Any]] = []
    adaptive_selection_rows: List[Dict[str, Any]] = []
    adaptive_gate_rows: List[Dict[str, Any]] = []
    for evaluation in evaluations:
        symbol = evaluation["symbol"]
        quality_rows.append(evaluation["data_quality"])
        provenance_rows.append(evaluation.get("data_provenance", {}))
        gate = evaluation.get("adaptive_research_gate", {})
        adaptive_gate_rows.append({
            "symbol": symbol,
            "status": gate.get("status"),
            "passed": gate.get("passed", False),
            "promotion_to_default": gate.get("promotion_to_default", False),
            **gate.get("checks", {}),
        })
        for model, metrics in evaluation.get("aggregates", {}).items():
            aggregate_rows.append({"symbol": symbol, **metrics})
        for window in evaluation.get("windows", []):
            for model, metrics in window["models"].items():
                window_rows.append({
                    "symbol": symbol,
                    "window": window["sequence"],
                    "validation_start": window["validation_start"],
                    "validation_end": window["validation_end"],
                    "model": model,
                    "lot_size": window["capital_feasibility"]["lot_size"],
                    "stage1_board_lot_feasible": window["capital_feasibility"]["stage1_board_lot_feasible"],
                    "estimated_minimum_cash_for_stage1": window["capital_feasibility"]["estimated_minimum_cash_for_stage1"],
                    **{
                        key: value
                        for key, value in metrics.items()
                        if key not in {
                            "trades",
                            "equity_curve",
                            "execution_log",
                            "adaptive_selection",
                        }
                    },
                })
                for trade in metrics.get("trades", metrics.get("execution_log", [])):
                    trade_rows.append({
                        "symbol": symbol,
                        "window": window["sequence"],
                        "model": model,
                        **trade,
                    })
            selection = window.get("adaptive_profile_selection", {})
            candidates = selection.get("candidate_results", [])
            if not candidates:
                adaptive_selection_rows.append({
                    "symbol": symbol,
                    "window": window["sequence"],
                    "validation_start": window["validation_start"],
                    "training_start": selection.get("training_start"),
                    "training_end": selection.get("training_end"),
                    "selection_status": selection.get("status"),
                    "selection_reason": selection.get("reason"),
                    "selected_profile_id": selection.get("selected_profile_id"),
                    "candidate_profile_id": None,
                    "selected": True,
                })
            for candidate in candidates:
                adaptive_selection_rows.append({
                    "symbol": symbol,
                    "window": window["sequence"],
                    "validation_start": window["validation_start"],
                    "training_start": selection.get("training_start"),
                    "training_end": selection.get("training_end"),
                    "selection_status": selection.get("status"),
                    "selection_reason": selection.get("reason"),
                    "selected_profile_id": selection.get("selected_profile_id"),
                    "candidate_profile_id": candidate.get("profile_id"),
                    "selected": candidate.get("profile_id")
                    == selection.get("selected_profile_id"),
                    **{key: value for key, value in candidate.items() if key != "profile_id"},
                })
        for regime, model_results in evaluation.get("regime_aggregates", {}).items():
            for model, metrics in model_results.items():
                regime_rows.append({
                    "symbol": symbol,
                    "regime": regime,
                    "model": model,
                    **metrics,
                })
        sensitivity = evaluation.get("parameter_sensitivity", {})
        for candidate in sensitivity.get("candidate_summaries", []):
            sensitivity_rows.append({
                "symbol": symbol,
                "selection_applied_to_validation": sensitivity.get(
                    "selection_applied_to_validation", False
                ),
                "average_return_range_pct_points": sensitivity.get(
                    "average_return_range_pct_points"
                ),
                "positive_candidate_fraction": sensitivity.get(
                    "positive_candidate_fraction"
                ),
                **candidate,
            })

    pd.DataFrame(aggregate_rows).to_csv(run_dir / "aggregate.csv", index=False, encoding="utf-8")
    pd.DataFrame(window_rows).to_csv(run_dir / "windows.csv", index=False, encoding="utf-8")
    pd.DataFrame(trade_rows).to_csv(run_dir / "trades.csv", index=False, encoding="utf-8")
    pd.DataFrame(quality_rows).to_csv(run_dir / "data_quality.csv", index=False, encoding="utf-8")
    pd.DataFrame(regime_rows).to_csv(run_dir / "regime_aggregate.csv", index=False, encoding="utf-8")
    pd.DataFrame(sensitivity_rows).to_csv(run_dir / "parameter_sensitivity.csv", index=False, encoding="utf-8")
    pd.DataFrame(provenance_rows).to_csv(run_dir / "data_provenance.csv", index=False, encoding="utf-8")
    pd.DataFrame(adaptive_selection_rows).to_csv(
        run_dir / "adaptive_profile_selection.csv", index=False, encoding="utf-8"
    )
    pd.DataFrame(adaptive_gate_rows).to_csv(
        run_dir / "adaptive_research_gate.csv", index=False, encoding="utf-8"
    )
    if universe_assessment is not None:
        pd.DataFrame([{
            key: value
            for key, value in universe_assessment.items()
            if key not in {"checks", "category_summary", "thresholds"}
        } | {
            f"threshold_{key}": value
            for key, value in universe_assessment.get("thresholds", {}).items()
        } | universe_assessment.get("checks", {})]).to_csv(
            run_dir / "universe_gate.csv", index=False, encoding="utf-8"
        )
        pd.DataFrame(universe_assessment.get("category_summary", [])).to_csv(
            run_dir / "universe_category_summary.csv", index=False, encoding="utf-8"
        )

    lines = [
        "# Walk-forward 台股歷史研究報告",
        "",
        "> 僅供個人市場研究與決策參考；所有訂單皆為歷史模擬。",
        "",
        f"- Run ID: `{run_dir.name}`",
        "- 未使用驗證資料最佳化；adaptive profile 僅由各驗證窗之前 504 個交易日選擇。",
        "- 報酬均已納入手續費、證交稅與滑價模型。",
        "- 行情分層為驗證後報告用途；參數敏感度僅使用驗證窗之前的資料。",
        "",
        "## 彙總結果",
        "",
        "| 標的 | 模型 | 複合報酬 | 最差視窗 MDD | 平均 Sharpe | 交易數 | 持倉天數率 | 平均資金使用率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in aggregate_rows:
        if row.get("status") != "available":
            continue
        lines.append(
            f"| {row['symbol']} | {row['model']} | {row.get('compounded_return_pct', 0):.2f}% "
            f"| {row.get('worst_window_drawdown_pct', 0):.2f}% "
            f"| {row.get('average_window_sharpe') if row.get('average_window_sharpe') is not None else '-'} "
            f"| {row.get('total_trades', 0)} | {row.get('exposure_pct', 0):.2f}% "
            f"| {row.get('average_capital_utilization_pct', 0):.2f}% |"
        )
    lines.extend([
        "",
        "## Adaptive research gate",
        "",
        "| 標的 | 狀態 | 報酬優於 legacy | MDD <= 12% | 熊市報酬 > -5% | 升格預設 |",
        "|---|---|---|---|---|---|",
    ])
    for row in adaptive_gate_rows:
        lines.append(
            f"| {row['symbol']} | {row.get('status')} "
            f"| {row.get('return_improves_legacy')} "
            f"| {row.get('worst_window_mdd_at_most_12pct')} "
            f"| {row.get('bear_compounded_return_above_minus_5pct')} "
            f"| {row.get('promotion_to_default')} |"
        )
    if universe_assessment is not None:
        maximum_mdd = universe_assessment.get("maximum_adaptive_mdd_pct")
        maximum_mdd_text = f"{maximum_mdd:.2f}%" if maximum_mdd is not None else "-"
        lines.extend([
            "",
            "## Universe research gate",
            "",
            f"- Universe: `{universe_assessment.get('universe_id')}`",
            f"- Status: `{universe_assessment.get('status')}`",
            f"- Usable symbols: {universe_assessment.get('usable_symbols')} / "
            f"{universe_assessment.get('total_symbols')}",
            f"- Gate pass rate: {universe_assessment.get('gate_pass_rate', 0):.2%}",
            f"- Maximum adaptive MDD: {maximum_mdd_text}",
            "- Promotion to default: false",
        ])
    lines.extend([
        "",
        "## 行情分層",
        "",
        "| 標的 | 行情 | 模型 | 視窗數 | 分層複合報酬 |",
        "|---|---|---|---:|---:|",
    ])
    for row in regime_rows:
        if row.get("status") not in {"available", "partial"}:
            continue
        lines.append(
            f"| {row['symbol']} | {row['regime']} | {row['model']} "
            f"| {row.get('window_count', 0)} | {row.get('compounded_return_pct', 0):.2f}% |"
        )
    lines.extend([
        "",
        "## 訓練窗參數敏感度",
        "",
        "| 標的 | 候選數 | 可用訓練窗 | 平均報酬範圍 | 正平均報酬候選比率 |",
        "|---|---:|---:|---:|---:|",
    ])
    for evaluation in evaluations:
        sensitivity = evaluation.get("parameter_sensitivity", {})
        available_candidates = [
            item
            for item in sensitivity.get("candidate_summaries", [])
            if item.get("status") == "available"
        ]
        window_counts = [item.get("training_window_count", 0) for item in available_candidates]
        lines.append(
            f"| {evaluation['symbol']} | {sensitivity.get('candidate_count', 0)} "
            f"| {max(window_counts) if window_counts else 0} "
            f"| {sensitivity.get('average_return_range_pct_points', '-')} 個百分點 "
            f"| {sensitivity.get('positive_candidate_fraction', '-')} |"
        )
    lines.extend([
        "",
        "## 資料品質",
        "",
        "| 標的 | 狀態 | 筆數 | 範圍 | 最大單日變動 | 價格還原契約 |",
        "|---|---|---:|---|---:|---|",
    ])
    for row in quality_rows:
        lines.append(
            f"| {row['symbol']} | {row['status']} | {row.get('row_count', 0)} "
            f"| {row.get('start_date', '-')} ~ {row.get('end_date', '-')} "
            f"| {row.get('max_abs_daily_return_pct') if row.get('max_abs_daily_return_pct') is not None else '-'}% "
            f"| {row.get('price_adjustment_contract', '-')} |"
        )
    lines.extend([
        "",
        "## 解讀限制",
        "",
        "- 資料涵蓋期間以本次快照與 data_provenance.csv 記錄的實際範圍為準。",
        "- Walk-forward 視窗重設初始資金；彙總報酬以各連續視窗報酬率複合計算。",
        "- 尚未納入股利、除權息還原差異、流動性衝擊與個股漲跌停成交限制。",
        "- yfinance 為非官方資料供應路徑；完整抓取契約與資料雜湊見 data_provenance.csv。",
        "- 供應商可能回溯修訂調整價；本次報告以內附 CSV 與 normalized_snapshot_sha256 固定版本。",
        "- 參數敏感度不會自動挑選或套用最佳參數，避免驗證資料洩漏。",
        "- adaptive profile 只改變配置比例，legacy 進退場訊號與風控規則維持不變。",
    ])
    (run_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir
