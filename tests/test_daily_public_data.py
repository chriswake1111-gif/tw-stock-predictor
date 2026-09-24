import math

import pytest

from src.collectors.daily_public_data import DailyPublicDataError, parse_daily_public_dataset


OBSERVED = "2026-09-11T09:00:00+08:00"


def payload(rows):
    return {"status": 200, "data": rows}


def price_row(**overrides):
    row = {
        "date": "2026-09-10", "stock_id": "2330", "Trading_Volume": 100,
        "Trading_money": 246500, "open": 2450, "max": 2480,
        "min": 2440, "close": 2465, "spread": 15,
    }
    row.update(overrides)
    return row


def test_price_parses_real_fields_and_marks_candidate_quality_warning():
    result = parse_daily_public_dataset("TaiwanStockPrice", payload([price_row()]), "2330.TW", OBSERVED)
    assert result["source"] == "FinMind"
    assert result["official_exchange_source"] is False
    assert result["rows"][0]["high"] == 2480
    assert result["rows"][0]["value"] == 246500
    assert result["quality_status"] == "quality_warning"


def test_eps_keeps_single_quarter_values_and_fails_closed_for_ttm():
    result = parse_daily_public_dataset("TaiwanStockFinancialStatements", payload([
        {"date": "2026-03-31", "stock_id": "2330", "type": "EPS", "value": 4.2},
        {"date": "2026-06-30", "stock_id": "2330", "type": "EPS", "value": 5.1},
        {"date": "2026-06-30", "stock_id": "2330", "type": "Revenue", "value": 10},
    ]), "2330.TW", OBSERVED)
    assert [r["quarterly_eps"] for r in result["rows"]] == [4.2, 5.1]
    assert result["rows"][0]["available_at"] == OBSERVED
    assert result["status"] == "insufficient_data"
    assert result["value"] is None
    assert result["reason"] == "share_basis_not_verified"


def test_per_converts_percent_and_does_not_zero_fill_missing_or_nonpositive():
    result = parse_daily_public_dataset("TaiwanStockPER", payload([{
        "date": "2026-09-10", "stock_id": "2330", "PER": 25.9,
        "PBR": 8.22, "dividend_yield": 1.07,
    }, {
        "date": "2026-09-09", "stock_id": "2330", "PER": None,
        "PBR": -1, "dividend_yield": None,
    }]), "2330.TW", OBSERVED)
    assert result["rows"][1]["yield_ratio"] == pytest.approx(0.0107)
    assert result["rows"][0]["pe"] is None
    assert result["rows"][0]["pb"] is None
    assert result["rows"][0]["yield_ratio"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_rejected(bad):
    with pytest.raises(DailyPublicDataError, match="non_finite"):
        parse_daily_public_dataset("TaiwanStockPrice", payload([price_row(close=bad)]), "2330.TW", OBSERVED)


def test_wrong_stock_id_is_rejected_even_when_symbol_suffix_matches():
    with pytest.raises(DailyPublicDataError, match="stock_id_mismatch"):
        parse_daily_public_dataset("TaiwanStockPrice", payload([price_row(stock_id="2317")]), "2330.TW", OBSERVED)


def test_conflicting_duplicate_date_is_rejected():
    with pytest.raises(DailyPublicDataError, match="duplicate_date_conflict"):
        parse_daily_public_dataset("TaiwanStockPrice", payload([price_row(), price_row(close=2466)]), "2330.TW", OBSERVED)


def test_future_date_negative_volume_and_zero_volume_behavior():
    with pytest.raises(DailyPublicDataError, match="future_date"):
        parse_daily_public_dataset("TaiwanStockPrice", payload([price_row(date="2026-09-12")]), "2330.TW", OBSERVED)
    with pytest.raises(DailyPublicDataError, match="negative_volume"):
        parse_daily_public_dataset("TaiwanStockPrice", payload([price_row(Trading_Volume=-1)]), "2330.TW", OBSERVED)
    result = parse_daily_public_dataset("TaiwanStockPrice", payload([price_row(Trading_Volume=0)]), "2330.TW", OBSERVED)
    assert result["rows"][0]["zero_volume"] is True
    assert result["quality_status"] == "quality_warning"


def test_payload_contract_and_dates_are_fail_closed():
    with pytest.raises(DailyPublicDataError, match="payload_status"):
        parse_daily_public_dataset("TaiwanStockPER", {"status": 500, "data": []}, "2330.TW", OBSERVED)
    with pytest.raises(DailyPublicDataError, match="invalid_quarter_end"):
        parse_daily_public_dataset("TaiwanStockFinancialStatements", payload([
            {"date": "2026-02-28", "stock_id": "2330", "type": "EPS", "value": 1}
        ]), "2330.TW", OBSERVED)


def empty_price_row(**overrides):
    return price_row(**{
        "date": "2026-09-09", "open": 0, "max": 0, "min": 0,
        "close": 0, "Trading_Volume": 0, "Trading_money": 0, "spread": 0,
        **overrides,
    })


def test_all_zero_activity_preserves_other_prices_and_discloses_missing_date():
    rows = [price_row(), empty_price_row(), empty_price_row()]
    before = [dict(row) for row in rows]
    result = parse_daily_public_dataset("TaiwanStockPrice", payload(rows), "2330.TW", OBSERVED)
    assert rows == before  # Raw source remains unchanged, including duplicates.
    assert [row["date"] for row in result["rows"]] == ["2026-09-10"]
    assert result["rows"][0]["close"] == 2465
    assert result["excluded_rows"] == [{
        "date": "2026-09-09", "reason": "source_all_zero_price_and_activity",
    }]
    assert result["status"] == "available"
    assert result["quality_status"] == "quality_warning"
    assert result["official_exchange_source"] is False


def test_only_all_zero_rows_are_insufficient_not_a_zero_price():
    result = parse_daily_public_dataset("TaiwanStockPrice", payload([empty_price_row()]), "2330.TW", OBSERVED)
    assert result["rows"] == []
    assert result["status"] == "insufficient_data"
    assert result["reason"] == "no_valid_price_rows"
    assert len(result["excluded_rows"]) == 1


@pytest.mark.parametrize("changes", [
    {"Trading_Volume": 1}, {"Trading_money": 1}, {"spread": 1}, {"close": 1},
    {"Trading_Volume": -1}, {"Trading_Volume": 0.5}, {"Trading_money": -1},
    {"open": None}, {"open": False}, {"open": float("nan")},
    {"stock_id": "3491"}, {"date": "2026-09-12"},
])
def test_exclusion_does_not_accept_mixed_invalid_or_untrusted_rows(changes):
    with pytest.raises(DailyPublicDataError):
        parse_daily_public_dataset("TaiwanStockPrice", payload([
            price_row(), empty_price_row(**changes),
        ]), "2330.TW", OBSERVED)


@pytest.mark.parametrize("reverse", [False, True])
def test_excluded_row_cannot_hide_conflicting_same_day_quote(reverse):
    rows = [empty_price_row(), price_row(date="2026-09-09")]
    with pytest.raises(DailyPublicDataError, match="duplicate_date_conflict"):
        parse_daily_public_dataset("TaiwanStockPrice", payload(rows[::-1] if reverse else rows), "2330.TW", OBSERVED)


def test_exclusions_have_stable_date_order_and_an_empty_feed_stays_insufficient():
    result = parse_daily_public_dataset("TaiwanStockPrice", payload([
        empty_price_row(date="2026-09-10"), empty_price_row(date="2026-09-08"),
    ]), "2330.TW", OBSERVED)
    assert [row["date"] for row in result["excluded_rows"]] == ["2026-09-08", "2026-09-10"]
    empty = parse_daily_public_dataset("TaiwanStockPrice", payload([]), "2330.TW", OBSERVED)
    assert empty["status"] == "insufficient_data" and empty["excluded_rows"] == []
