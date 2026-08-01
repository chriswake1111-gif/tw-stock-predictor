"""跨 API、Dashboard 與排程器共用的研究分析契約。"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from src.collectors.finmind_collector import FinMindCollector
from src.collectors.market_turnover_collector import MarketTurnoverCollector
from src.collectors.twse_collector import TWSECollector
from src.engine.ma_deduction import MADeductionEngine
from src.engine.market_sentiment import MarketSentimentEngine
from src.engine.valuation_eva import ValuationEVAEngine
from src.engine.wave_fibonacci import WaveFibonacciEngine


RESEARCH_DISCLAIMER = (
    "僅供個人市場研究與決策參考，不構成投資建議、報酬保證或真實委託指令。"
)


def normalize_symbol(symbol: str) -> str:
    """將台股代號正規化為資料源使用格式。"""
    clean = symbol.strip().upper()
    if clean in {"0000", "TAIEX"}:
        return "^TWII"
    if not (clean.startswith("^") or ".TW" in clean or ".TWO" in clean):
        return f"{clean}.TW"
    return clean


def _json_number(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return float(value)


def analyze_symbol(
    symbol: str,
    db_path: str = "data/cache.db",
    config_path: str = "config/config.yaml",
    as_of_date: Optional[str] = None,
    start_date: Optional[str] = None,
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """使用同一資料與計算契約產生研究分析；不包含任何真實交易能力。"""
    real_symbol = normalize_symbol(symbol)
    end_date = str(as_of_date or datetime.now().strftime("%Y-%m-%d"))
    if start_date is None:
        start_date = (
            datetime.strptime(end_date[:10], "%Y-%m-%d") - timedelta(days=365 * 2)
        ).strftime("%Y-%m-%d")

    twse = TWSECollector(db_path=db_path)
    df = twse.get_ohlcv(real_symbol, start_date=start_date, end_date=end_date)
    if df.empty:
        return ({
            "status": "insufficient_data",
            "mode": "research_only",
            "symbol": real_symbol,
            "input_symbol": symbol,
            "as_of_date": end_date,
            "reason": "No OHLCV data is available for the requested period",
            "disclaimer": RESEARCH_DISCLAIMER,
        }, pd.DataFrame())

    clean_df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    clean_df["date"] = clean_df["date"].astype(str)
    clean_df = clean_df[clean_df["date"] <= end_date].sort_values("date").reset_index(drop=True)
    if clean_df.empty:
        return ({
            "status": "insufficient_data",
            "mode": "research_only",
            "symbol": real_symbol,
            "input_symbol": symbol,
            "as_of_date": end_date,
            "reason": "No OHLCV data is available on or before as_of_date",
            "disclaimer": RESEARCH_DISCLAIMER,
        }, pd.DataFrame())

    wave_engine = WaveFibonacciEngine(config_path=config_path)
    ma_engine = MADeductionEngine(config_path=config_path)
    val_engine = ValuationEVAEngine(config_path=config_path)
    finmind = FinMindCollector(db_path=db_path)
    turnover_collector = MarketTurnoverCollector(db_path=db_path)
    sentiment_engine = MarketSentimentEngine(db_path=db_path, config_path=config_path)

    realtime_wave = wave_engine.get_realtime_confirmed_wave_params(
        real_symbol, clean_df, as_of_date=end_date
    )
    hindsight_wave = wave_engine.get_hindsight_wave_params(real_symbol, clean_df)

    analyzed_df = ma_engine.calculate_ma_and_deductions(clean_df)
    analyzed_df["resonance_signal"] = ma_engine.detect_resonance_signal(analyzed_df)
    is_resonance = bool(analyzed_df["resonance_signal"].iloc[-1])

    clean_code = real_symbol.replace(".TW", "").replace(".TWO", "").replace("^", "")
    if real_symbol.startswith("^"):
        ttm_res = {
            "status": "not_applicable",
            "reason": "Index symbols do not have per-share EPS",
            "eps": None,
        }
    else:
        ttm_res = finmind.get_ttm_eps(clean_code, as_of_date=end_date)

    eps_contract = ttm_res.get("eps") or {
        "value": None,
        "type": "historical_ttm",
        "unit": "TWD_per_share",
        "source": "FinMind TaiwanStockFinancialStatements",
        "calculation": "sum_latest_four_consecutive_reported_quarter_eps",
        "status": ttm_res.get("status", "insufficient_data"),
        "reason": ttm_res.get("reason"),
    }

    valuation_contract: Dict[str, Any] = {
        "status": "insufficient_data",
        "estimated_eps": eps_contract.get("value"),
        "cheap_price": None,
        "fair_price": None,
        "expensive_price": None,
        "reason": "A valid positive TTM EPS is required for PE valuation",
    }
    if eps_contract.get("value") is not None:
        valuation_contract = val_engine.calculate_dog_master_valuation(
            eps=float(eps_contract["value"])
        )

    eva_contract = {
        "status": "unsupported",
        "reason": "Required NOPAT, invested capital and share-count contracts are not implemented",
        "eva_floor_price": None,
    }

    valuation_df = (
        finmind.get_valuation(clean_code, end_date=end_date)
        if clean_code and not real_symbol.startswith("^")
        else pd.DataFrame()
    )
    if valuation_df.empty:
        screener_contract = {
            "status": "insufficient_data",
            "passed": None,
            "reason": "No PE/PB/dividend-yield record is available",
        }
    else:
        latest_valuation = valuation_df.sort_values("date").iloc[-1]
        screener_contract = {
            "status": "available",
            "date": str(latest_valuation["date"]),
            **val_engine.screen_two_lows_one_high(
                pe=float(latest_valuation["pe"]),
                pb=float(latest_valuation["pb"]),
                yield_rate=float(latest_valuation["yield_rate"]),
            ),
        }

    turnover_res = turnover_collector.get_latest_available_turnover(end_date=end_date)
    turnover_info = turnover_res.get("market_turnover")
    turnover_twd = turnover_info.get("value") if turnover_info else None
    sentiment_contract = sentiment_engine.check_turnover_m1b_overheat(
        market_turnover_twd=turnover_twd,
        as_of_date=end_date,
    )
    sentiment_contract["market_turnover_detail"] = turnover_info

    latest_row = analyzed_df.iloc[-1]
    ma_deductions: Dict[str, Any] = {}
    for period in [8, 13, 21, 55, 144]:
        sma_col = f"SMA_{period}"
        deduct_col = f"deduct_val_{period}"
        slope_col = f"ma_slope_up_{period}"
        if all(col in analyzed_df.columns for col in [sma_col, deduct_col, slope_col]):
            ma_deductions[str(period)] = {
                "sma": _json_number(latest_row[sma_col]),
                "deduct_val": _json_number(latest_row[deduct_col]),
                "slope_up": bool(latest_row[slope_col]),
            }

    kline_data = [
        {
            "time": str(row["date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": _json_number(row.get("volume")),
        }
        for _, row in clean_df.iterrows()
    ]

    return ({
        "status": "available",
        "mode": "research_only",
        "symbol": real_symbol,
        "input_symbol": symbol,
        "as_of_date": end_date,
        "latest_price": round(float(latest_row["close"]), 2),
        "date": str(latest_row["date"]),
        "is_resonance": is_resonance,
        "eps": eps_contract,
        "wave_analysis": {
            "realtime_confirmed": realtime_wave,
            "hindsight_visualization": hindsight_wave,
        },
        "valuation": valuation_contract,
        "eva_valuation": eva_contract,
        "two_lows_one_high": screener_contract,
        "sentiment": sentiment_contract,
        "ma_deductions": ma_deductions,
        "kline_data": kline_data,
        "disclaimer": RESEARCH_DISCLAIMER,
        "execution_capability": "none",
    }, analyzed_df)
