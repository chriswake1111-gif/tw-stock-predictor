import json
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from src.collectors.installed_egress_client import EndpointNotAllowlistedError, validate_egress_url
from src.repositories.migration_runner import apply_valuation_migration
from src.services import daily_public_data_service as service_module
from src.services.daily_public_data_service import DATASETS, DailyPublicDataService


OBSERVED = "2026-09-11T09:00:00+08:00"


def price_payload(code="2330"):
    return {"status": 200, "data": [{
        "date": "2026-09-10", "stock_id": code, "Trading_Volume": 100,
        "Trading_money": 246500, "open": 2450, "max": 2480,
        "min": 2440, "close": 2465, "spread": 15,
    }]}


def per_payload(code="2330"):
    return {"status": 200, "data": [{
        "date": "2026-09-10", "stock_id": code, "PER": 25.9,
        "PBR": 8.22, "dividend_yield": 1.07,
    }]}


def eps_payload(code="2330"):
    return {"status": 200, "data":[
        {"date":"2026-03-31", "stock_id":code, "type":"EPS", "value":4.2},
    ]}


class FakeClient:
    def __init__(self, failures=(), payloads=None):
        self.failures = set(failures)
        self.payloads = payloads or {}

    def fetch(self, url, *, deadline_monotonic):
        dataset = parse_qs(urlparse(url).query)["dataset"][0]
        if dataset in self.failures:
            return 503, b"{}", {}
        payload = self.payloads.get(dataset) or {
            "TaiwanStockPrice": price_payload(),
            "TaiwanStockPER": per_payload(),
            "TaiwanStockFinancialStatements": eps_payload(),
        }[dataset]
        return 200, json.dumps(payload, separators=(",", ":")).encode(), {}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "daily.sqlite"
    apply_valuation_migration(str(path))
    return str(path)


def refresh(service, client, authorize=lambda resource: None):
    return service.refresh("2330.TW", "op-1", client, authorize, 999999.0)


def counts(db_path):
    with sqlite3.connect(db_path) as conn:
        return tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                     for table in ("daily_public_snapshots", "daily_public_attempts"))


def test_refresh_authorizes_before_snapshot_write_and_stores_three_sources(db_path):
    events = []
    def authorize(resource):
        dataset = resource.split(".", 1)[1]
        with sqlite3.connect(db_path) as conn:
            snapshots = conn.execute("SELECT COUNT(*) FROM daily_public_snapshots WHERE dataset=?", (dataset,)).fetchone()[0]
        events.append((resource, snapshots))
    assert refresh(DailyPublicDataService(db_path), FakeClient(), authorize) == []
    snapshots, attempts = counts(db_path)
    assert snapshots == 3 and attempts == 3
    first_for_resource = {}
    for resource, snapshot_count in events:
        first_for_resource.setdefault(resource, snapshot_count)
    assert len(first_for_resource) == 3
    assert all(snapshot_count == 0 for snapshot_count in first_for_resource.values())


def test_same_raw_content_reuses_snapshot_but_adds_attempts(db_path, monkeypatch):
    svc = DailyPublicDataService(db_path)
    assert refresh(svc, FakeClient()) == []
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE daily_public_attempts SET checked_at='2020-01-01T00:00:00Z'")
        conn.commit()
    assert refresh(svc, FakeClient()) == []
    assert counts(db_path) == (3, 6)


def test_failure_preserves_previous_rows_and_marks_latest_attempt_failed(db_path):
    svc = DailyPublicDataService(db_path)
    refresh(svc, FakeClient())
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE daily_public_attempts SET checked_at='2020-01-01T00:00:00Z'")
        conn.commit()
    assert "TaiwanStockPrice:source_http_503" in refresh(svc, FakeClient(failures={"TaiwanStockPrice"}))
    view = svc.view("2330.TW", "9999-01-01T00:00:00Z")
    assert view["TaiwanStockPrice"]["rows"]
    assert view["TaiwanStockPrice"]["last_update_status"] == "failed"


def test_cutoff_before_observed_hides_snapshots(db_path):
    svc = DailyPublicDataService(db_path)
    refresh(svc, FakeClient())
    view = svc.view("2330.TW", "2000-01-01T00:00:00Z")
    assert all(item["rows"] == [] and item["reason"] == "not_collected" for item in view.values())


def test_authorization_failure_prevents_any_write(db_path):
    def revoked(_resource):
        raise RuntimeError("revoked")
    with pytest.raises(RuntimeError, match="revoked"):
        refresh(DailyPublicDataService(db_path), FakeClient(), revoked)
    assert counts(db_path) == (0, 0)


def test_one_source_failure_keeps_other_source_snapshots(db_path):
    svc = DailyPublicDataService(db_path)
    errors = refresh(svc, FakeClient(failures={"TaiwanStockPER"}))
    assert errors == ["TaiwanStockPER:source_http_503"]
    view = svc.view("2330.TW", "9999-01-01T00:00:00Z")
    assert view["TaiwanStockPrice"]["rows"]
    assert view["TaiwanStockFinancialStatements"]["rows"]
    assert view["TaiwanStockPER"]["rows"] == []


def test_finmind_allowlist_accepts_exact_bounded_query():
    url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPER&data_id=2330&start_date=2025-09-11&end_date=2026-09-11"
    assert validate_egress_url(url) == url


@pytest.mark.parametrize("url", [
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPER&data_id=2330&start_date=2025-09-11&end_date=2026-09-11&token=x",
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPER&dataset=TaiwanStockPER&data_id=2330&start_date=2025-09-11&end_date=2026-09-11",
    "https://evil.example/api/v4/data?dataset=TaiwanStockPER&data_id=2330&start_date=2025-09-11&end_date=2026-09-11",
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPER&data_id=2330&start_date=2024-01-01&end_date=2026-09-11",
])
def test_finmind_allowlist_rejects_out_of_contract_urls(url):
    with pytest.raises(EndpointNotAllowlistedError):
        validate_egress_url(url)


def test_excluded_price_remains_in_immutable_raw_snapshot(db_path):
    original = price_payload()
    original["data"].append({
        "date": "2026-09-09", "stock_id": "2330", "Trading_Volume": 0,
        "Trading_money": 0, "open": 0, "max": 0, "min": 0, "close": 0, "spread": 0,
    })
    svc = DailyPublicDataService(db_path)
    assert refresh(svc, FakeClient(payloads={"TaiwanStockPrice": original})) == []
    current = svc.view("2330.TW", "9999-01-01T00:00:00Z")["TaiwanStockPrice"]
    assert len(current["rows"]) == 1
    assert current["excluded_rows"][0]["date"] == "2026-09-09"
    assert current["parser_version"] == "daily-price-v2"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT raw_json, normalized_json FROM daily_public_snapshots WHERE snapshot_id=?", (current["snapshot_id"],)).fetchone()
    assert json.loads(row[0]) == original
    assert json.loads(row[1])["quality_status"] == "quality_warning"
    assert svc.view("2330.TW", "9999-01-01T00:00:00Z")["TaiwanStockPER"]["parser_version"] == "daily-public-v1"


def test_new_price_parser_refreshes_recent_v1_and_keeps_old_cutoff_readable(db_path, monkeypatch):
    class PriceOnly(DailyPublicDataService):
        datasets = ("TaiwanStockPrice",)

    class LegacyPrice(PriceOnly):
        def parse(self, *args):
            result = super().parse(*args)
            result.pop("excluded_rows")
            return result

    now = datetime.now(timezone.utc)
    before = (now - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
    after = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(service_module, "utc_now_timestamp", lambda: before)
    with monkeypatch.context() as legacy:
        legacy.setattr(service_module, "PRICE_PARSER_VERSION", "daily-public-v1")
        refresh(LegacyPrice(db_path), FakeClient())
    with sqlite3.connect(db_path) as conn:
        old_row = conn.execute("SELECT * FROM daily_public_snapshots").fetchone()
    monkeypatch.setattr(service_module, "utc_now_timestamp", lambda: after)
    svc = PriceOnly(db_path)
    assert refresh(svc, FakeClient()) == []
    old_view = svc.view("2330.TW", before)["TaiwanStockPrice"]
    new_view = svc.view("2330.TW", after)["TaiwanStockPrice"]
    assert old_view["parser_version"] == "daily-public-v1" and "excluded_rows" not in old_view
    assert new_view["parser_version"] == "daily-price-v2"
    assert old_view["snapshot_id"] != new_view["snapshot_id"]
    assert old_view["raw_sha256"] == new_view["raw_sha256"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT * FROM daily_public_snapshots WHERE snapshot_id=?", (old_row[0],)).fetchone() == old_row
    assert counts(db_path) == (2, 2)
    # A repeat within the existing freshness window reuses the v2 result.
    assert refresh(svc, FakeClient(failures={"TaiwanStockPrice"})) == []
    assert counts(db_path) == (2, 2)
