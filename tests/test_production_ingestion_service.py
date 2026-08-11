import json
import sqlite3

import pytest
import requests

from src.domain.data_foundation import (
    IngestionRun,
    PublicationEvidenceStatus,
    PublicationVerificationMode,
    ResourcePublicationEvidence,
    TriggerType,
    sha256_text,
)
from src.domain.liquidity import MarketTurnoverObservation
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.liquidity_repository import LiquidityRepository
from src.services.market_liquidity_service import MarketLiquidityService
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


def publication_evidence(
    *,
    release="2026-06-25T16:00:00+08:00",
    evidence_text="cbc release evidence v1",
):
    return {
        "official_release_at": release,
        "source_reference": "https://example.test/cbc/official-release",
        "source_identity": "CBC official publication notice",
        "evidence_file_sha256": sha256_text(evidence_text),
        "captured_at": "2026-06-25T16:05:00+08:00",
        "verification_mode": "manual_official_source_review",
        "verified_by": "internal.researcher",
        "status": "accepted",
    }


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


def test_turnover_http_error_is_recorded_without_discarding_other_market(tmp_path):
    def fetch(url, timeout):
        return Response(status_code=503) if "twse.com.tw" in url else Response([TPEX_ROW])

    result = ProductionIngestionService(
        str(tmp_path / "http.db"), fetch
    ).ingest_official_turnover("2026-08-11", observed_at=OBSERVED)
    assert result["status"] == "partial"
    failed = next(item for item in result["items"] if item["provider_id"] == "twse")
    assert failed["status"] == "provider_error"
    assert failed["http_status"] == 503
    assert result["turnover"]["tpex_turnover_twd"] == 20_000


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


def test_cbc_bare_release_timestamp_is_not_authoritative_evidence(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    db_path = str(tmp_path / "data.db")
    result = ProductionIngestionService(db_path).ingest_cbc_m1b(
        payload,
        {"2026-05": "2026-06-25T16:00:00+08:00"},
        observed_at="2026-08-11T10:00:00+08:00",
    )
    assert result["status"] == "blocked"
    assert result["candidate_periods"] == ["2026-05"]
    assert result["records"] == []
    assert LiquidityRepository(db_path).latest_m1b_as_of(
        "2026-08-11T02:01:00Z"
    ) is None


def test_cbc_verified_publication_evidence_enables_asof_record(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    db_path = str(tmp_path / "verified.db")
    result = ProductionIngestionService(db_path).ingest_cbc_m1b(
        payload,
        {"2026-05": publication_evidence()},
        observed_at="2026-08-11T10:00:00+08:00",
    )
    assert result["status"] == "succeeded"
    assert result["candidate_periods"] == []
    assert result["records"][0]["available_at"] == "2026-06-25T08:00:00.000000Z"
    evidence = result["publication_evidence"][0]
    assert evidence["evidence_file_sha256"] == sha256_text(
        "cbc release evidence v1"
    )
    assert result["records"][0]["publication_evidence_id"] == evidence[
        "publication_evidence_id"
    ]
    assert LiquidityRepository(db_path).latest_m1b_as_of(
        "2026-06-25T07:59:59Z"
    ) is None
    assert LiquidityRepository(db_path).latest_m1b_as_of(
        "2026-08-11T02:01:00Z"
    )["period"] == "2026-05"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM valuation_approvals").fetchone()[0] == 0


def test_cbc_candidate_can_be_promoted_only_by_explicit_release_metadata(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    db_path = str(tmp_path / "promotion.db")
    service = ProductionIngestionService(db_path)
    candidate = service.ingest_cbc_m1b(payload, {}, observed_at=OBSERVED)
    promoted = service.ingest_cbc_m1b(
        payload,
        {"2026-05": publication_evidence()},
        observed_at="2026-08-11T10:05:00+08:00",
    )
    assert candidate["status"] == "blocked"
    assert promoted["status"] == "succeeded"
    assert promoted["records"][0]["period"] == "2026-05"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT eligibility_status, supersedes_revision_id
            FROM raw_resource_revisions ORDER BY ingested_at
            """
        ).fetchall()
    assert rows[0] == ("awaiting_review", None)
    assert rows[1][0] == "eligible"
    assert rows[1][1] is not None


def test_changed_cbc_publication_evidence_is_append_only(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    db_path = str(tmp_path / "evidence-revision.db")
    service = ProductionIngestionService(db_path)
    first = service.ingest_cbc_m1b(
        payload, {"2026-05": publication_evidence()}, observed_at=OBSERVED
    )
    second = service.ingest_cbc_m1b(
        payload,
        {"2026-05": publication_evidence(evidence_text="cbc release evidence v2")},
        observed_at="2026-08-11T10:05:00+08:00",
    )
    assert first["records"][0]["revision"] == 1
    assert second["records"][0]["revision"] == 2
    with sqlite3.connect(db_path) as conn:
        evidence_rows = conn.execute(
            """
            SELECT revision_number, supersedes_evidence_id
            FROM resource_publication_evidence ORDER BY revision_number
            """
        ).fetchall()
        assert evidence_rows[0] == (1, None)
        assert evidence_rows[1][0] == 2
        assert evidence_rows[1][1] is not None
        assert conn.execute("SELECT COUNT(*) FROM cbc_m1b_monthly").fetchone()[0] == 2


def test_cbc_revocation_is_asof_safe_and_reaccept_requires_new_m1b(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    db_path = str(tmp_path / "publication-lifecycle.db")
    service = ProductionIngestionService(db_path)
    liquidity = LiquidityRepository(db_path)

    accepted = service.ingest_cbc_m1b(
        payload,
        {"2026-05": publication_evidence(evidence_text="accepted-v1")},
        observed_at="2026-08-11T10:00:00+08:00",
    )
    m1 = accepted["records"][0]
    turnover = liquidity.add_turnover(
        MarketTurnoverObservation(
            trade_date="2026-08-11",
            twse_turnover_twd=800_000_000,
            tpex_turnover_twd=100_000_000,
            twse_source="TWSE",
            tpex_source="TPEx",
            twse_dataset="exchangeReport/FMTQIK",
            tpex_dataset="tpex_daily_trading_index",
            available_at="2026-08-11T02:05:00Z",
            fetched_at="2026-08-11T02:05:30Z",
        ),
        ingested_at="2026-08-11T02:05:30Z",
    )
    before_revoke = MarketLiquidityService(db_path).analyze(
        "2026-08-11T02:06:00Z"
    )

    revoked_evidence = publication_evidence(evidence_text="revoked-v2")
    revoked_evidence["status"] = "revoked"
    revoked = service.ingest_cbc_m1b(
        payload,
        {"2026-05": revoked_evidence},
        observed_at="2026-08-11T10:10:00+08:00",
    )

    assert before_revoke["status"] == "available"
    assert before_revoke["turnover_m1b_ratio_pct"] == 3.0
    assert revoked["status"] == "blocked"
    assert revoked["records"] == []
    assert liquidity.latest_m1b_as_of("2026-08-11T02:09:59Z")["id"] == m1["id"]
    assert liquidity.latest_m1b_as_of("2026-08-11T02:10:00Z") is None
    assert liquidity.m1b_for_turnover(
        turnover, "2026-08-11T02:09:59Z"
    )["id"] == m1["id"]
    assert liquidity.m1b_for_turnover(
        turnover, "2026-08-11T02:10:00Z"
    ) is None
    after_revoke = MarketLiquidityService(db_path).analyze(
        "2026-08-11T02:11:00Z"
    )
    assert after_revoke["status"] == "insufficient_data"
    assert after_revoke["reason"] == "m1b_unavailable_for_latest_turnover"

    corrected_input = publication_evidence(evidence_text="accepted-v3")
    corrected_evidence = DataFoundationRepository(db_path).add_publication_evidence(
        ResourcePublicationEvidence(
            provider_id="cbc",
            resource_id="cbc.m1b",
            logical_revision_key="2026-05",
            official_release_at=corrected_input["official_release_at"],
            source_reference=corrected_input["source_reference"],
            source_identity=corrected_input["source_identity"],
            evidence_file_sha256=corrected_input["evidence_file_sha256"],
            captured_at=corrected_input["captured_at"],
            verification_mode=PublicationVerificationMode(
                corrected_input["verification_mode"]
            ),
            verified_by=corrected_input["verified_by"],
            status=PublicationEvidenceStatus.ACCEPTED,
        ),
        ingested_at="2026-08-11T02:20:00Z",
    )
    assert liquidity.latest_m1b_as_of("2026-08-11T02:20:00Z") is None

    corrected = service.ingest_cbc_m1b(
        payload,
        {"2026-05": corrected_input},
        observed_at="2026-08-11T10:21:00+08:00",
    )
    m2 = corrected["records"][0]
    latest = liquidity.latest_m1b_as_of("2026-08-11T02:22:00Z")
    historical = liquidity.latest_m1b_as_of("2026-08-11T02:09:59Z")
    recovered = MarketLiquidityService(db_path).analyze("2026-08-11T02:22:00Z")

    assert m2["revision"] == 2
    assert m2["id"] != m1["id"]
    assert m2["publication_evidence_id"] != m1["publication_evidence_id"]
    assert m2["publication_evidence_id"] == corrected_evidence[
        "publication_evidence_id"
    ]
    assert latest["id"] == m2["id"]
    assert historical["id"] == m1["id"]
    assert recovered["status"] == "available"
    with sqlite3.connect(db_path) as conn:
        stored_m1 = conn.execute(
            "SELECT status, publication_evidence_id FROM cbc_m1b_monthly WHERE id = ?",
            (m1["id"],),
        ).fetchone()
        assert stored_m1 == ("available", m1["publication_evidence_id"])
        assert conn.execute("SELECT COUNT(*) FROM analysis_snapshots").fetchone()[0] == 0


def test_cbc_future_or_unknown_publication_metadata_is_rejected_before_writes(tmp_path):
    with open("tests/fixtures/cbc_ef15m01_response.json", encoding="utf-8") as source:
        payload = json.load(source)
    for publication_map in (
        {"2026-05": "2026-08-12T00:00:00Z"},
        {"2026-06": publication_evidence()},
    ):
        db_path = str(tmp_path / f"bad-{len(publication_map)}-{next(iter(publication_map))}.db")
        result = ProductionIngestionService(db_path).ingest_cbc_m1b(
            payload, publication_map, observed_at=OBSERVED
        )
        assert result["status"] == "failed"
        assert result["items"][0]["status"] == "rejected"
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM raw_resource_revisions").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM cbc_m1b_monthly").fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM resource_publication_evidence"
            ).fetchone()[0] == 0


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


def test_failed_run_retry_is_traceable_without_duplicate_accepted_revisions(tmp_path):
    db_path = str(tmp_path / "retry.db")
    service = ProductionIngestionService(
        db_path,
        turnover_fetcher(
            requests.ConnectionError("twse down"),
            requests.ConnectionError("tpex down"),
        ),
    )
    failed = service.ingest_official_turnover(
        "2026-08-11", observed_at="2026-08-11T01:00:00Z"
    )
    assert failed["status"] == "failed"
    service.fetcher = turnover_fetcher([TWSE_ROW], [TPEX_ROW])
    retried = service.ingest_official_turnover(
        "2026-08-11",
        observed_at="2026-08-11T02:00:00Z",
        trigger_type=TriggerType.RETRY,
        retry_of_run_id=failed["run_id"],
    )
    duplicate_retry = service.ingest_official_turnover(
        "2026-08-11",
        observed_at="2026-08-11T02:05:00Z",
        trigger_type=TriggerType.RETRY,
        retry_of_run_id=failed["run_id"],
    )
    assert retried["status"] == "succeeded"
    assert duplicate_retry["turnover"]["id"] == retried["turnover"]["id"]
    with sqlite3.connect(db_path) as conn:
        retry_row = conn.execute(
            "SELECT trigger_type, retry_of_run_id FROM ingestion_runs WHERE ingestion_run_id = ?",
            (retried["run_id"],),
        ).fetchone()
        assert retry_row == ("retry", failed["run_id"])
        assert conn.execute("SELECT COUNT(*) FROM raw_resource_revisions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM market_turnover_daily").fetchone()[0] == 1


def test_late_correction_does_not_replace_latest_logical_business_date(tmp_path):
    db_path = str(tmp_path / "late-correction.db")
    twse_rows = [
        {"Date": "115/08/10", "TradeValue": "300,000"},
        {"Date": "115/08/11", "TradeValue": "310,000"},
    ]
    tpex_rows = [
        {"Date": "115/08/10", "TradeAmount": "20,000"},
        {"Date": "115/08/11", "TradeAmount": "21,000"},
    ]
    service = ProductionIngestionService(
        db_path, turnover_fetcher(twse_rows, tpex_rows)
    )
    service.ingest_official_turnover(
        "2026-08-10", observed_at="2026-08-10T02:00:00Z"
    )
    service.ingest_official_turnover(
        "2026-08-11", observed_at="2026-08-11T02:00:00Z"
    )
    service.fetcher = turnover_fetcher(
        [{"Date": "115/08/10", "TradeValue": "305,000"}],
        [{"Date": "115/08/10", "TradeAmount": "20,500"}],
    )
    service.ingest_official_turnover(
        "2026-08-10", observed_at="2026-08-12T02:00:00Z"
    )
    health = service.foundation.provider_health_as_of(
        "2026-08-12T03:00:00Z", resource_id="twse.market-turnover"
    )[0]
    assert health["last_eligible_logical_key"] == "2026-08-11"


def test_calendar_effective_revision_collapse_handles_corrections_and_revoke(tmp_path):
    trading_row = {
        "Name": "開始交易", "Date": "1150812", "Weekday": "三",
        "Description": "開始交易",
    }
    holiday_row = {
        "Name": "休市放假", "Date": "1150812", "Weekday": "三",
        "Description": "依規定放假",
    }

    trading_to_holiday = ProductionIngestionService(
        str(tmp_path / "trading-holiday.db")
    )
    trading_to_holiday.ingest_twse_calendar(
        [trading_row], observed_at="2026-08-11T01:00:00Z"
    )
    trading_to_holiday.ingest_twse_calendar(
        [holiday_row], observed_at="2026-08-11T02:00:00Z"
    )
    assert trading_to_holiday.foundation.provider_health_as_of(
        "2026-08-11T03:00:00Z", resource_id="twse.market-turnover"
    )[0]["latest_expected_trade_date"] is None

    holiday_to_trading = ProductionIngestionService(
        str(tmp_path / "holiday-trading.db")
    )
    holiday_to_trading.ingest_twse_calendar(
        [holiday_row], observed_at="2026-08-11T01:00:00Z"
    )
    holiday_to_trading.ingest_twse_calendar(
        [trading_row], observed_at="2026-08-11T02:00:00Z"
    )
    assert holiday_to_trading.foundation.provider_health_as_of(
        "2026-08-11T03:00:00Z", resource_id="twse.market-turnover"
    )[0]["latest_expected_trade_date"] == "2026-08-12"

    trading_to_revoked = ProductionIngestionService(
        str(tmp_path / "trading-revoked.db")
    )
    first = trading_to_revoked.ingest_twse_calendar(
        [trading_row], observed_at="2026-08-11T01:00:00Z"
    )["calendar_revisions"][0]
    trading_to_revoked.foundation.add_calendar_revision(
        calendar_revision_id="calendar.revoked",
        raw_resource_revision_id=first["raw_resource_revision_id"],
        market="TW", trade_date="2026-08-12", session_status="trading",
        available_at="2026-08-11T02:00:00Z",
        ingested_at="2026-08-11T02:00:00Z",
        status="revoked", note="official correction revoked session",
    )
    assert trading_to_revoked.foundation.provider_health_as_of(
        "2026-08-11T03:00:00Z", resource_id="twse.market-turnover"
    )[0]["latest_expected_trade_date"] is None
