from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.collectors.eod_close_collectors import (
    EodSourceContractError,
    OfficialEodCollector,
    parse_approved_partial_fixture,
    parse_tpex_snapshot,
    parse_twse_snapshot,
)
from src.collectors.twse_isin_classification_collector import (
    parse_twse_isin_classification,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_twse_eod_parser_keeps_official_close_and_raw_strings() -> None:
    parsed = parse_twse_snapshot(_json("eod_twse_stock_day_all.json"))

    assert parsed.status == "available"
    assert parsed.coverage_state == "complete"
    assert parsed.source_trade_date == "2026-08-27"
    row = parsed.rows[0]
    assert row.official_code == "2330"
    assert row.raw_close_text == "1005.00"
    assert row.close_value == "1005"
    assert row.raw_volume_text == "123456789"
    assert row.trade_indication_value == "123456789000"


def test_tpex_eod_parser_uses_close_and_excludes_phase13_resource() -> None:
    parsed = parse_tpex_snapshot(_json("eod_tpex_daily_close_quotes.json"))

    assert parsed.resource_id == "tpex.eod.daily_close_quotes"
    assert parsed.rows[0].official_code == "6488"
    assert parsed.rows[0].raw_close_text == "500.00"
    assert "tpex_mainboard_quotes" not in parsed.resource_id


def test_official_eod_collector_uses_allowlisted_get_and_raw_body_hash() -> None:
    body = (FIXTURES / "eod_twse_stock_day_all.json").read_bytes()
    calls = []

    class Response:
        content = body

        def raise_for_status(self):
            return None

        def json(self):
            return json.loads(body.decode("utf-8"))

    def fetcher(url, timeout):
        calls.append((url, timeout))
        return Response()

    result = OfficialEodCollector(fetcher, timeout=7).fetch(
        "TWSE", received_at="2026-08-27T05:00:00Z"
    )

    assert calls == [(
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", 7
    )]
    assert result["raw_payload_sha256"] == hashlib.sha256(body).hexdigest()
    assert result["parsed"].source_trade_date == "2026-08-27"


@pytest.mark.parametrize("payload", [None, {}, {"Date": "115/08/27"}])
def test_live_parser_fails_closed_on_non_array_or_schema_drift(payload) -> None:
    with pytest.raises(EodSourceContractError) as error:
        parse_twse_snapshot(payload)
    assert error.value.reason == "schema_changed"


def test_empty_source_is_unknown_and_not_invented_partial() -> None:
    parsed = parse_twse_snapshot([])

    assert parsed.status == "unknown"
    assert parsed.coverage_state == "unknown"
    assert parsed.reason == "source_empty"
    assert parsed.rows == ()


def test_partial_branch_requires_explicit_fixture_proof() -> None:
    parsed = parse_approved_partial_fixture(
        _json("eod_partial_venue_fixture.json"), venue="TWSE"
    )

    assert parsed.status == "partial"
    assert parsed.coverage_state == "partial"
    assert parsed.reason == "partial_venue_payload"


def test_isin_parser_requires_exact_code_market_and_type() -> None:
    html = (FIXTURES / "eod_isin_supported.html").read_text(encoding="utf-8")
    parsed = parse_twse_isin_classification(
        html,
        official_code="2330",
        expected_market="上市",
        trade_date="2026-08-27",
    )

    assert parsed.state == "accepted"
    assert parsed.decision == "supported_stock"
    assert parsed.official_code_raw == "2330"
    assert parsed.market_raw == "上市"
    assert parsed.security_type_raw == "股票"
    assert parsed.listing_date == "2000-09-05"


def test_isin_parser_does_not_infer_from_code_when_evidence_is_missing() -> None:
    parsed = parse_twse_isin_classification(
        "<table><tr><td>Security Code</td><td>2330</td></tr></table>",
        official_code="2330",
        expected_market="上市",
    )

    assert parsed.state == "schema_changed"
    assert parsed.decision == "needs_human_input"
    assert parsed.reason == "classification_evidence_missing"
