from __future__ import annotations

from src.domain.eod_close import (
    classify_product,
    compose_context,
    eod_close_status_matrix_v1,
    normalize_decimal_text,
    parse_twse_roc_date,
)


def test_status_matrix_uses_blocked_then_human_then_partial_precedence() -> None:
    assert eod_close_status_matrix_v1(["partial_venue_payload"])["status"] == "partial"
    assert eod_close_status_matrix_v1(
        ["partial_venue_payload", "classification_evidence_missing"]
    )["status"] == "needs_human_input"
    assert eod_close_status_matrix_v1(
        ["classification_evidence_missing", "schema_changed"]
    )["status"] == "blocked"


def test_non_available_context_never_exposes_close_value() -> None:
    context = compose_context(
        reasons=["official_zero_volume_not_public_eligible"],
        freshness_state="current",
        current_complete=False,
        evidence_complete=False,
        close_value="1005",
        canonical_symbol="2330.TW",
        instrument_id="instrument-1",
        official_code="2330",
        venue="TWSE",
        security_type="股票",
        knowledge_cutoff_at=None,
        evaluated_at="2026-08-27T04:00:00Z",
        selection_scope=None,
        selected_trade_date="2026-08-27",
        currency="TWD",
        unit="TWD_per_share",
        price_semantics="official_reported_close_v1",
        provider="TWSE",
        resource_key="twse.eod.stock_day_all",
        source_url="https://example.invalid",
        source_trade_date="2026-08-27",
        observed_at="2026-08-27T04:00:00Z",
        source_scope="twse_whole_market_daily_close",
    )
    assert context["status"] == "insufficient_data"
    assert context["close_value"] is None
    assert context["currency"] is None
    assert context["unit"] is None


def test_decimal_and_roc_date_helpers_are_exact_and_float_free() -> None:
    normalized, parsed, state = normalize_decimal_text("1,005.00")
    assert normalized == "1005"
    assert str(parsed) == "1005.00"
    assert state == "valid"
    assert parse_twse_roc_date("115/08/27") == "2026-08-27"
    assert parse_twse_roc_date("1150827") == "2026-08-27"


def test_product_classifier_only_accepts_exact_stock_market() -> None:
    accepted = classify_product(
        official_code="2330",
        requested_code="2330",
        market="上市",
        expected_market="上市",
        security_type="股票",
        currency="新台幣",
    )
    etf = classify_product(
        official_code="0050",
        requested_code="0050",
        market="上市",
        expected_market="上市",
        security_type="ETF",
    )
    inferred = classify_product(
        official_code="2330",
        requested_code="2330",
        market=None,
        expected_market="上市",
        security_type=None,
    )
    assert accepted["product_scope"] == "supported_stock"
    assert etf["product_scope"] == "not_applicable"
    assert inferred["product_scope"] == "needs_human_input"
