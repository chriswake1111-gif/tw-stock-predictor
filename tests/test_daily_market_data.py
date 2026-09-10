import json
from src.services.daily_market_data_service import DailyMarketDataService
from src.repositories.migration_runner import apply_valuation_migration


def test_official_market_sources_share_cache_and_keep_independent_failures(tmp_path):
    db = str(tmp_path / "market.db")
    apply_valuation_migration(db)
    service = DailyMarketDataService(db)
    calls = []
    class Client:
        def fetch(self, url, **kwargs):
            calls.append(url)
            if "EF15M01" in url:
                return 503, b"{}", {}
            key = "TradeAmount" if "tpex" in url else "TradeValue"
            return 200, json.dumps([{"Date": "1150909", key: "100"}]).encode(), {}
    errors = service.refresh("MARKET", "op1", Client(), lambda _: None, 999999)
    assert errors == ["CBC_M1B:source_http_503"]
    data = service.view("MARKET", "2099-01-01T00:00:00Z")
    assert data["TWSE_TURNOVER"]["rows"] == [{"date": "2026-09-09", "value": 100}]
    assert data["TPEX_TURNOVER"]["last_update_status"] == "available"
    assert data["CBC_M1B"]["last_update_status"] == "failed"
    service.refresh("MARKET", "op2", Client(), lambda _: None, 999999)
    assert len(calls) == 4  # only failed CBC retries, shared successful markets do not
    assert service.view("MARKET", "2000-01-01T00:00:00Z")["TWSE_TURNOVER"]["rows"] == []


def test_cbc_selects_named_m1b_category_not_first_column(tmp_path):
    service = DailyMarketDataService(str(tmp_path / "unused.db"))
    payload = {"result": {"structure": {"tables": [{"name": "Table1", "items": ["M1A", "M1B"]}]},
                          "data": [["2026M07", "12", "0", "30", "0"]]}}
    parsed = service.parse("CBC_M1B", payload, "MARKET", "2026-09-11T00:00:00Z")
    assert parsed["rows"][0]["value"] == 30000000
    assert parsed["publication_instant_verified"] is False
