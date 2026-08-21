from __future__ import annotations

from src.collectors.universe_collectors import EXCLUDED_RESOURCE_KEYS, UniverseCollector, UniverseSourceRejected, assert_approved_resource, parse_universe_payload


def test_approved_source_roles_and_quote_exclusion():
    assert assert_approved_resource("twse.t187ap03_L")
    assert assert_approved_resource("tpex.mopsfin_t187ap03_O")
    assert "tpex_mainboard_quotes" in EXCLUDED_RESOURCE_KEYS
    try:
        assert_approved_resource("tpex_mainboard_quotes")
    except UniverseSourceRejected as exc:
        assert str(exc) == "quote_source_excluded"
    else:
        raise AssertionError("quote source was accepted")


def test_master_parser_keeps_leading_zero_and_rejects_malformed_schema():
    rows = parse_universe_payload("twse.t187ap03_L", [{"出表日期": "1150820", "公司代號": "0050", "公司名稱": "元大台灣50"}])
    assert rows[0]["official_code"] == "0050"


def test_collector_assigns_first_observation_only_after_success_and_rejects_price_fields():
    class Response:
        status_code = 200

        def json(self):
            return [{"出表日期": "1150820", "公司代號": "2330", "公司名稱": "台積電"}]

    result = UniverseCollector(lambda *_args, **_kwargs: Response()).fetch_official(
        "twse.t187ap03_L", url="https://www.twse.com.tw/fixture",
        fetched_at="2026-08-21T00:00:00Z", received_at="2026-08-21T00:00:01Z",
    )
    assert result.status == "accepted"
    assert result.first_observed_at == "2026-08-21T00:00:01Z"

    class PriceResponse(Response):
        def json(self):
            return [{"出表日期": "1150820", "公司代號": "2330", "公司名稱": "台積電", "收盤價": "100"}]

    rejected = UniverseCollector(lambda *_args, **_kwargs: PriceResponse()).fetch_official(
        "twse.t187ap03_L", url="https://www.twse.com.tw/fixture",
        fetched_at="2026-08-21T00:00:00Z", received_at="2026-08-21T00:00:01Z",
    )
    assert rejected.status == "schema_changed"
    assert rejected.reason == "price_field_excluded"


def test_collector_transport_error_is_provider_error_and_https_is_required():
    result = UniverseCollector(lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("offline"))).fetch_official(
        "tpex.mopsfin_t187ap03_O", url="https://www.tpex.org.tw/fixture",
    )
    assert result.status == "provider_error"
    with __import__("pytest").raises(UniverseSourceRejected, match="official_source_requires_https"):
        UniverseCollector(lambda *_args, **_kwargs: None).fetch_official(
            "tpex.mopsfin_t187ap03_O", url="http://www.tpex.org.tw/fixture",
        )
