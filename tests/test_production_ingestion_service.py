import json
import sqlite3

import pytest
import requests

from src.domain.data_foundation import IngestionRun, TriggerType
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.liquidity_repository import LiquidityRepository
from src.services.production_ingestion_service import ProductionIngestionService


class Response:
    def __init__(self, payload=None, status_code=200, error=None):
        self.payload = payload
        self.status_code = status_code
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def json(self):
        return self.payload


def turnover_fetcher(twse_payload, tpex_payload):
    def fetch(url, timeout):
        if "twse.com.tw" in url:
            value = twse_payload
        else:
            value = tpex_payload
        if isinstance(value, Exception):
            return Response(error=value)
        return Response(value)
    return fetch


TWSE_ROW = {"Date": "115/08/11", "TradeValue": "300,000"}
TPEX_ROW = {"Date": "115/08/11", "TradeAmount": "20,000"}
OBSERVED = "2026-08-11T10:00:00+08:00"


@pytest.mark.parametrize(
    ("twse", "tpex", "expected_twse", "expected_tpex"),
    [
        ([TWSE_ROW], requests.ConnectionError("down"), 300_000.0, None),
        (requests.ConnectionError("down"), [TPEX_ROW], None, 20_000.0),
    ],
)
def test_turnover_provider_failure_preserves_independent_partial(
    tmp_path, twse, tpex, expected_twse, expected_tpex
):
    service = ProductionIngestionService(
        str(tmp_path / "data.db"), turnover_fetcher(twse, tpex)
    )
    result = service.ingest_official_turnover(
        "2026-08-11", observed_at=OBSERVED
    )
    assert result["status"] == "partial"
    assert result["turnover"]["status"] == "partial"
    assert result["turnover"]["twse_turnover_twd"] == expected_twse
    assert result["turnover"]["tpex_turnover_twd"] == expected_tpex
    assert {item["status"] for item in result["items"]} == {
        "accepted", "provider_error"
    }


def test_turnover_duplicate_is_idempotent_and_correction_is_append_only(tmp_path):
    db_path = str(tmp_path / "data.db")
    service = ProductionIngestionService(
        db_path, turnover_fetcher([TWSE_ROW], [TPEX_ROW])
    )
    first = service.ingest_official_turnover("2026-08-11", observed_at=OBSERVED)
    duplicate = service.ingest_official_turnover(
        "2026-08-11", observed_at="2026-08-11T10:05:00+08:00"
    )
    assert first["turnover"]["id"] == duplicate["turnover"]["id"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_resource_revisions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM market_turnover_daily").fetchone()[0] == 1

    corrected = {**TWSE_ROW, "TradeValue": "310,000"}
    service.fetcher = turnover_fetcher([corrected], [TPEX_ROW])
    result = service.ingest_official_turnover(
        "2026-08-11", observed_at="2026-08-11T10:10:00+08:00"
    )
    assert result["turnover"]["revision"] == 2
    assert result["turnover"]["twse_turnover_twd"] == 310_000
    with sqlite3.connect(db_path) as conn:
        revision = conn.execute(
            """
            SELECT supersedes_revision_id FROM raw_resource_revisions
            WHERE provider_id = 'twse' ORDER BY ingested_at DESC LIMIT 1
            """
        ).fetchone()
    assert revision[0] is not None


def test_cbc_missing_release_time_stays_candidate_and_never_enters_m1b(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    db_path = str(tmp_path / "data.db")
    service = ProductionIngestionService(db_path)
    result = service.ingest_cbc_m1b(payload, {}, observed_at=OBSERVED)
    assert result["status"] == "blocked"
    assert result["candidate_periods"] == ["2026-05"]
    assert result["records"] == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cbc_m1b_monthly").fetchone()[0] == 0
        raw = conn.execute(
            "SELECT eligibility_status, available_at FROM raw_resource_revisions"
        ).fetchone()
    assert raw == ("awaiting_review", None)


def test_cbc_authoritative_release_map_enables_asof_record(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    db_path = str(tmp_path / "data.db")
    result = ProductionIngestionService(db_path).ingest_cbc_m1b(
        payload,
        {"2026-05": "2026-06-25T16:00:00+08:00"},
        observed_at="2026-08-11T10:00:00+08:00",
    )
    assert result["status"] == "succeeded"
    assert result["candidate_periods"] == []
    assert result["records"][0]["available_at"] == "2026-06-25T08:00:00.000000Z"
    assert LiquidityRepository(db_path).latest_m1b_as_of(
        "2026-06-25T07:59:59Z"
    ) is None
    assert LiquidityRepository(db_path).latest_m1b_as_of(
        "2026-08-11T02:01:00Z"
    )["period"] == "2026-05"


def test_official_calendar_uses_explicit_session_meaning_and_cutoff(tmp_path):
    payload = [
        {"Name": "中華民國開國紀念日", "Date": "1150101", "Weekday": "四", "Description": "依規定放假1日"},
        {"Name": "國曆新年開始交易日", "Date": "1150102", "Weekday": "五", "Description": "國曆新年開始交易"},
        {"Name": "農曆春節前最後交易日", "Date": "1150211", "Weekday": "三", "Description": "最後交易日"},
        {"Name": "農曆春節前調整放假", "Date": "1150212", "Weekday": "四", "Description": "市場無交易"},
    ]
    db_path = str(tmp_path / "data.db")
    service = ProductionIngestionService(db_path)
    result = service.ingest_twse_calendar(payload, observed_at=OBSERVED)
    assert result["status"] == "succeeded"
    repo = DataFoundationRepository(db_path)
    assert repo.calendar_session_as_of(
        "TW", "2026-01-01", "2026-08-11T01:59:59Z"
    ) is None
    assert repo.calendar_session_as_of(
        "TW", "2026-01-01", "2026-08-11T02:00:00Z"
    )["session_status"] == "holiday"
    assert repo.calendar_session_as_of(
        "TW", "2026-01-02", "2026-08-11T02:00:00Z"
    )["session_status"] == "trading"
    assert repo.calendar_session_as_of(
        "TW", "2026-02-11", "2026-08-11T02:00:00Z"
    )["session_status"] == "special"
    assert repo.calendar_session_as_of(
        "TW", "2026-02-12", "2026-08-11T02:00:00Z"
    )["session_status"] == "no_trading"


def test_calendar_schema_drift_fails_closed_and_releases_lock(tmp_path):
    db_path = str(tmp_path / "data.db")
    service = ProductionIngestionService(db_path)
    result = service.ingest_twse_calendar(
        [{"Date": "1150101"}], observed_at=OBSERVED
    )
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "schema_changed"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trading_calendar_revisions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ingestion_resource_locks").fetchone()[0] == 0


def test_provider_failure_and_overlap_are_visible_in_run_ledger(tmp_path):
    db_path = str(tmp_path / "data.db")
    service = ProductionIngestionService(
        db_path,
        lambda _url, timeout: Response(error=requests.ConnectionError("down")),
    )
    failed = service.fetch_twse_calendar(observed_at=OBSERVED)
    assert failed["status"] == "failed"
    assert failed["items"][0]["quality_status"] == "provider_error"

    holder = IngestionRun(
        ingestion_run_id="run_lock_holder",
        started_at="2026-08-11T02:01:00Z",
        trigger_type=TriggerType.MANUAL,
        runner_version="test",
        requested_resources=("twse.trading-calendar",),
        actor_id="internal.test",
    )
    service.foundation.add_run(holder)
    service.foundation.acquire_resource_lock(
        "twse.trading-calendar", holder.ingestion_run_id,
        "2026-08-11T02:01:00Z",
    )
    blocked = service.ingest_twse_calendar(
        [{"Name": "開始交易", "Date": "1150102", "Weekday": "五", "Description": "開始交易"}],
        observed_at="2026-08-11T02:02:00Z",
    )
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "resource_ingestion_already_locked"
