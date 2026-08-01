from datetime import datetime, timedelta

import pandas as pd

from src.analysis_service import analyze_symbol
from src.collectors.cbc_collector import CBCCollector


def _synthetic_ohlcv(days: int = 400) -> pd.DataFrame:
    dates = [
        (datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(days)
    ]
    prices = [100.0 + i * 0.2 + (i % 20) for i in range(days)]
    return pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [price + 1 for price in prices],
        "low": [price - 1 for price in prices],
        "close": prices,
        "volume": [10000 + i for i in range(days)],
    })


def test_shared_analysis_contract_is_research_only_and_as_of_safe(tmp_path, monkeypatch):
    full_df = _synthetic_ohlcv()
    as_of_date = full_df.iloc[349]["date"]
    monkeypatch.setattr(
        "src.analysis_service.TWSECollector.get_ohlcv",
        lambda self, symbol, start_date, end_date: full_df,
    )
    monkeypatch.setattr(
        "src.analysis_service.FinMindCollector.get_ttm_eps",
        lambda self, stock_id, as_of_date=None: {
            "status": "available",
            "eps": {
                "status": "available",
                "value": 10.0,
                "type": "historical_ttm",
                "period_end": "2024-09-30",
                "available_at": "2024-11-10",
                "unit": "TWD_per_share",
                "source": "test",
            },
        },
    )
    monkeypatch.setattr(
        "src.analysis_service.FinMindCollector.get_valuation",
        lambda self, stock_id, end_date=None: pd.DataFrame([{
            "date": "2024-11-10", "pe": 12.0, "pb": 1.2, "yield_rate": 0.05
        }]),
    )
    monkeypatch.setattr(
        "src.analysis_service.MarketTurnoverCollector.get_latest_available_turnover",
        lambda self, end_date=None: {
            "status": "available",
            "market_turnover": {"value": 450_000_000_000.0, "trade_date": end_date},
        },
    )
    monkeypatch.setattr(
        "src.collectors.cbc_collector.CBCCollector.get_latest_m1b",
        lambda self, as_of_date=None: {
            "status": "available",
            "value": 270_000.0,
            "period": "2024-10",
            "available_at": "2024-11-25",
            "unit": "TWD_100_million",
            "source": "test",
        },
    )

    result, analyzed_df = analyze_symbol(
        "2330",
        db_path=str(tmp_path / "analysis.db"),
        as_of_date=as_of_date,
    )

    assert result["status"] == "available"
    assert result["mode"] == "research_only"
    assert result["execution_capability"] == "none"
    assert result["date"] <= as_of_date
    assert analyzed_df["date"].max() <= as_of_date
    assert result["valuation"]["status"] == "available"
    assert result["two_lows_one_high"]["passed"] is True


def test_cbc_m1b_respects_publication_date(tmp_path):
    collector = CBCCollector(db_path=str(tmp_path / "cbc.db"))
    collector.save_m1b_data(
        "2025-01-31", 270_000.0, period_str="2025-01", available_at_str="2025-02-25"
    )

    before_release = collector.get_latest_m1b(as_of_date="2025-02-20")
    after_release = collector.get_latest_m1b(as_of_date="2025-02-25")

    assert before_release["status"] == "insufficient_data"
    assert after_release["status"] == "available"
    assert after_release["available_at"] == "2025-02-25"
