from datetime import date, timedelta

from src.domain.liquidity import M1BMonthlyObservation, MarketTurnoverObservation
from src.engine.liquidity import LiquidityEngine
from src.repositories.liquidity_repository import LiquidityRepository
from src.services.market_liquidity_service import MarketLiquidityService


def test_ratio_rolling_percentile_and_reference_case():
    engine = LiquidityEngine()
    assert engine.ratio_pct(990, 30_000) == 3.3
    assert engine.rolling_mean([float(i) for i in range(1, 21)], 20) == (10.5, 20)
    assert engine.rolling_mean([1.0] * 19, 20) == (None, 19)
    assert engine.rolling_mean([2.0] * 60, 60) == (2.0, 60)
    assert engine.rolling_mean([2.0] * 59, 60) == (None, 59)
    assert engine.percentile(2.0, [1.0, 2.0, 2.0, 3.0]) == 75.0
    start = date(2020, 1, 1)
    rows = [
        {"trade_date": (start + timedelta(days=i)).isoformat(), "ratio_pct": 1.0}
        for i in range(2200)
    ]
    rows[-1]["ratio_pct"] = 3.35
    result = engine.evaluate(rows, rows[-1]["trade_date"])
    assert result["alert_level"] == "reference_extreme_case"
    short = engine.evaluate(rows[-100:], rows[-1]["trade_date"])
    assert short["historical_percentile_5y"] is None
    assert short["historical_percentile_10y"] is None
    future = {"trade_date": "2035-01-01", "ratio_pct": 99.0}
    without_future = engine.evaluate(rows + [future], rows[-1]["trade_date"])
    assert without_future["trade_date"] == rows[-1]["trade_date"]
    assert without_future["ratio_pct"] == 3.35


def test_complete_partial_and_no_future_data(tmp_path):
    db = str(tmp_path / "liquidity.db")
    repo = LiquidityRepository(db)
    repo.add_m1b(M1BMonthlyObservation(
        period="2026-05", value_raw=30_000, raw_unit="TWD_million",
        data_date="2026-05-31", available_at="2026-06-25T08:00:00Z",
        fetched_at="2026-06-25T09:00:00Z", source="CBC",
        source_dataset="CBC EF15M01",
    ), ingested_at="2026-06-25T09:01:00Z")
    complete = MarketTurnoverObservation(
        trade_date="2026-06-26", twse_turnover_twd=800_000_000,
        tpex_turnover_twd=100_000_000, twse_source="TWSE", tpex_source="TPEx",
        twse_dataset="exchangeReport/FMTQIK", tpex_dataset="tpex_daily_trading_index",
        available_at="2026-06-26T09:00:00Z", fetched_at="2026-06-26T09:01:00Z",
    )
    future_partial = MarketTurnoverObservation(
        trade_date="2026-06-27", twse_turnover_twd=900_000_000,
        tpex_turnover_twd=None, twse_source="TWSE", tpex_source=None,
        twse_dataset="exchangeReport/FMTQIK", tpex_dataset=None,
        available_at="2026-06-27T09:00:00Z", fetched_at="2026-06-27T09:01:00Z",
    )
    repo.add_turnover(complete, ingested_at="2026-06-26T09:02:00Z")
    repo.add_turnover(future_partial, ingested_at="2026-06-27T09:02:00Z")
    result = MarketLiquidityService(db).analyze("2026-06-26T10:00:00Z")
    assert result["status"] == "available"
    assert result["trade_date"] == "2026-06-26"
    assert result["turnover_m1b_ratio_pct"] == 3.0
    assert [rule["rule_id"] for rule in result["rules_used"]] == ["LIQ-01", "LIQ-02"]
    assert "LIQ-03" not in str(result)
    assert not {"sell_signal", "market_top", "automatic_order"} & set(result)


def test_latest_partial_is_not_hidden_by_older_complete_observation(tmp_path):
    db = str(tmp_path / "liquidity.db")
    repo = LiquidityRepository(db)
    repo.add_m1b(M1BMonthlyObservation(
        period="2026-05", value_raw=30_000, raw_unit="TWD_million",
        data_date="2026-05-31", available_at="2026-06-25T08:00:00Z",
        fetched_at="2026-06-25T09:00:00Z", source="CBC",
        source_dataset="CBC EF15M01",
    ), ingested_at="2026-06-25T09:01:00Z")
    repo.add_turnover(MarketTurnoverObservation(
        trade_date="2026-06-26", twse_turnover_twd=800_000_000,
        tpex_turnover_twd=100_000_000, twse_source="TWSE", tpex_source="TPEx",
        twse_dataset="exchangeReport/FMTQIK", tpex_dataset="tpex_daily_trading_index",
        available_at="2026-06-26T09:00:00Z", fetched_at="2026-06-26T09:01:00Z",
    ), ingested_at="2026-06-26T09:02:00Z")
    repo.add_turnover(MarketTurnoverObservation(
        trade_date="2026-06-27", twse_turnover_twd=900_000_000,
        tpex_turnover_twd=None, twse_source="TWSE", tpex_source=None,
        twse_dataset="exchangeReport/FMTQIK", tpex_dataset=None,
        available_at="2026-06-27T09:00:00Z", fetched_at="2026-06-27T09:01:00Z",
    ), ingested_at="2026-06-27T09:02:00Z")

    result = MarketLiquidityService(db).analyze("2026-06-27T10:00:00Z")

    assert result["status"] == "partial"
    assert result["trade_date"] == "2026-06-27"
    assert result["turnover_m1b_ratio_pct"] is None
    assert result["latest_complete_observation"]["trade_date"] == "2026-06-26"
    assert result["latest_complete_observation"]["turnover_m1b_ratio_pct"] == 3.0


def test_only_one_market_is_partial(tmp_path):
    repo = LiquidityRepository(str(tmp_path / "liquidity.db"))
    repo.add_turnover(MarketTurnoverObservation(
        trade_date="2026-07-01", twse_turnover_twd=800_000_000,
        tpex_turnover_twd=None, twse_source="TWSE", tpex_source=None,
        twse_dataset="exchangeReport/FMTQIK", tpex_dataset=None,
        available_at="2026-07-01T09:00:00Z", fetched_at="2026-07-01T09:01:00Z",
    ), ingested_at="2026-07-01T09:02:00Z")
    result = MarketLiquidityService(str(tmp_path / "liquidity.db")).analyze(
        "2026-07-02T00:00:00Z"
    )
    assert result["status"] == "partial"
    assert result["reason"] == "both_twse_and_tpex_turnover_are_required"
    assert result["turnover_twd"]["total"] is None


def test_missing_twse_is_also_partial(tmp_path):
    repo = LiquidityRepository(str(tmp_path / "liquidity.db"))
    repo.add_turnover(MarketTurnoverObservation(
        trade_date="2026-07-01", twse_turnover_twd=None,
        tpex_turnover_twd=100_000_000, twse_source=None, tpex_source="TPEx",
        twse_dataset=None, tpex_dataset="tpex_daily_trading_index",
        available_at="2026-07-01T09:00:00Z", fetched_at="2026-07-01T09:01:00Z",
    ), ingested_at="2026-07-01T09:02:00Z")
    result = MarketLiquidityService(str(tmp_path / "liquidity.db")).analyze(
        "2026-07-02T00:00:00Z"
    )
    assert result["status"] == "partial"
    assert result["turnover_twd"]["twse"] is None
    assert result["turnover_m1b_ratio_pct"] is None


def test_revoked_turnover_revision_does_not_restore_old_revision(tmp_path):
    db = str(tmp_path / "liquidity.db")
    repo = LiquidityRepository(db)
    common = {
        "trade_date": "2026-07-01",
        "available_at": "2026-07-01T09:00:00Z",
        "fetched_at": "2026-07-01T09:01:00Z",
    }
    repo.add_turnover(MarketTurnoverObservation(
        **common, twse_turnover_twd=800_000_000, tpex_turnover_twd=100_000_000,
        twse_source="TWSE", tpex_source="TPEx",
        twse_dataset="exchangeReport/FMTQIK", tpex_dataset="tpex_daily_trading_index",
    ), ingested_at="2026-07-01T09:02:00Z")
    repo.add_turnover(MarketTurnoverObservation(
        **{**common, "available_at": "2026-07-02T09:00:00Z"},
        twse_turnover_twd=None, tpex_turnover_twd=None,
        twse_source=None, tpex_source=None, twse_dataset=None, tpex_dataset=None,
        revision=2, status="revoked",
    ), ingested_at="2026-07-02T09:02:00Z")

    rows = repo.turnover_as_of("2026-07-03T00:00:00Z")
    result = MarketLiquidityService(db).analyze("2026-07-03T00:00:00Z")

    assert len(rows) == 1
    assert rows[0]["revision"] == 2
    assert rows[0]["status"] == "revoked"
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "latest_market_turnover_revoked"
