"""Fixed anonymous inputs, read transactions and fail-closed source qualification."""
import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.collectors.daily_public_data import parse_daily_public_dataset
from src.domain.analysis_snapshot import canonical_json
from src.services.wave_qualification_service import WaveQualificationService, calendar_quality, price_quality
from src.repositories.universe_repository import UniverseRepository
from tests.test_research_guidance import db  # noqa: F401
from tests.test_local_assumption_api import api  # noqa: F401

SYMBOL = "9911.TWO"
CUTOFF = "2026-09-04T00:00:00Z"
OBSERVED = "2026-09-03T08:00:00Z"
DATASET = "TaiwanStockPrice"
BOUNDS = dict(start="2026-09-01", end="2026-09-03")


def seed(path, *, name="price-one", observed=OBSERVED, mutate=None, corrupt_hash=False, source_url=None):
    raw = dict(status=200, fixture_revision=name, data=[dict(stock_id="9911", date=f"2026-09-0{d}", open=100,
        max=102, min=98, close=101, Trading_Volume=1000, Trading_money=101000, spread=1) for d in (1, 2, 3)])
    data = parse_daily_public_dataset(DATASET, raw, SYMBOL, observed)
    if mutate:
        mutate(raw, data)
    texts = [canonical_json(v) for v in (raw, data)]
    url = source_url or "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=9911&start_date=2026-09-01&end_date=2026-09-03"
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO daily_public_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)", (
            name, SYMBOL, DATASET, observed, url, "broken" if corrupt_hash else hashlib.sha256(texts[0].encode()).hexdigest(),
            texts[0], texts[1], hashlib.sha256(texts[1].encode()).hexdigest(), "daily-price-v2"))
    return data


def states(result):
    return {c["id"]: c for c in result["checks"]}


def test_missing_database_is_not_created_and_flag_off(tmp_path, monkeypatch):
    path = tmp_path / "absent.db"
    assert WaveQualificationService(path).get(SYMBOL)["status"] == "unavailable"
    assert not path.exists()
    for flag in ("RESEARCH_WAVE_ASSIST_ENABLED", "RESEARCH_WAVE_QUALIFICATION_ENABLED"):
        with monkeypatch.context() as patch:
            patch.setenv(flag, "false")
            assert WaveQualificationService(path).get(SYMBOL) == dict(contract_version="wave_qualification_v1", enabled=False, symbol=SYMBOL)
    with pytest.raises(ValueError):
        WaveQualificationService(path).get("../bad")


def test_price_read_does_not_promote_or_mutate(db, monkeypatch):
    seed(db)
    monkeypatch.setattr(UniverseRepository, "contexts_for_symbols_with_connection", lambda *a, **k:
        {SYMBOL: dict(identity_status="resolved", venue="TPEX", identity={})})
    before = open(db, "rb").read()
    result = WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)
    assert result["status"] == "quality_warning"
    assert result["requested_range"] == result["actual_range"] == BOUNDS
    checks = states(result)
    assert checks["source"]["status"] == checks["prices"]["status"] == "passed"
    assert all(checks[k]["status"] == "unknown" for k in ("sessions", "basis", "availability", "confirmation"))
    assert result["automatic_candidates_eligible"] is False
    assert all(len(c["samples"]) <= 8 and len(c["evidence"]) <= 8 for c in result["checks"])
    assert open(db, "rb").read() == before


@pytest.mark.parametrize("change", ["hash", "wrong_symbol", "prices", "basis", "duplicate", "untrusted_url"])
def test_corrupt_and_inconsistent_snapshots_fail_closed(db, change):
    def mutate(raw, data):
        if change == "wrong_symbol": data["symbol"] = "9912"
        if change == "prices": data["rows"][0]["low"] = 200
        if change == "basis": data["adjusted"] = True
        if change == "duplicate": raw["data"].append(copy.deepcopy(raw["data"][0]))
    seed(db, mutate=mutate, corrupt_hash=change == "hash",
         source_url="https://untrusted.invalid/run?cmd=delete" if change == "untrusted_url" else None)
    result = WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)
    if change == "hash":
        assert result["status"] == "unavailable" and result["snapshot"] is None
    else:
        key = "source" if change in {"wrong_symbol", "untrusted_url"} else "basis" if change == "basis" else "prices"
        assert states(result)[key]["status"] == "failed"


def test_latest_corruption_never_uses_older_snapshot(db):
    seed(db)
    seed(db, name="price-two", observed="2026-09-03T09:00:00Z", corrupt_hash=True)
    assert WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)["status"] == "unavailable"
    assert WaveQualificationService(db).get(SYMBOL, cutoff="2026-09-03T08:30:00Z")["snapshot"]["snapshot_id"] == "price-one"


def events():
    common = dict(available_at="2026-08-01T00:00:00Z", ingested_at="2026-08-01T00:00:00Z", status="accepted", effective_at="2026-08-01T00:00:00Z")
    return [dict(common, lifecycle_event_id="listed", event_type="listed")], [dict(common, operational_event_id="normal", trading_state="normal")]


def calendars():
    return [dict(trade_date=f"2026-09-0{d}", status="available", session_status="trading", raw_eligible=True, calendar_revision_id=f"cal-{d}") for d in (1, 2, 3)]


def test_calendar_distinguishes_missing_unknown_and_holiday():
    life, ops = events()
    rows = [dict(date=f"2026-09-0{d}") for d in (1, 2, 3)]
    assert calendar_quality(rows, BOUNDS, calendars(), life, ops, CUTOFF)["status"] == "passed"
    assert calendar_quality(rows[:-1], BOUNDS, calendars(), life, ops, CUTOFF)["counts"]["missing"] == 1
    assert calendar_quality(rows, BOUNDS, calendars()[:-1], life, ops, CUTOFF)["status"] == "unknown"
    assert calendar_quality(rows, BOUNDS, calendars(), [], [], CUTOFF)["status"] == "unknown"
    cal = calendars(); cal[2]["session_status"] = "holiday"
    assert calendar_quality(rows[:-1], BOUNDS, cal, life, ops, CUTOFF)["status"] == "passed"
    assert calendar_quality(rows, BOUNDS, cal, life, ops, CUTOFF)["counts"]["conflicts"] == 1


def test_suspension_resume_and_revocation_are_distinct():
    life, ops = events()
    suspend = dict(ops[0], operational_event_id="suspended", trading_state="suspended", effective_at="2026-09-02T00:00:00Z")
    resume = dict(life[0], lifecycle_event_id="resumed", event_type="resumed", effective_at="2026-09-03T00:00:00Z")
    rows = [dict(date=f"2026-09-0{d}") for d in (1, 3)]
    result = calendar_quality(rows, BOUNDS, calendars(), life+[resume], ops+[suspend], CUTOFF)
    assert result["status"] == "passed" and result["counts"]["suspended"] == 1
    suspend["status"] = "revoked"
    assert calendar_quality(rows, BOUNDS, calendars(), life+[resume], ops+[suspend], CUTOFF)["status"] == "unknown"


def test_zero_volume_duplicates_and_missing_values():
    rows = [dict(date="2026-09-01", open=100, high=102, low=98, close=101, volume=1000)]
    assert price_quality(rows, rows, [], BOUNDS)["status"] == "passed"
    for field, value in (("volume", 0), ("low", None), ("open", float("nan")), ("volume", True)):
        changed = [dict(rows[0], **{field:value})]
        assert price_quality(changed, changed, [], BOUNDS)["status"] == "failed"
    assert price_quality(rows, rows * 2, [], BOUNDS)["status"] == "failed"


def test_same_read_transaction_ignores_mid_read_insertion(db, monkeypatch):
    seed(db)
    with sqlite3.connect(db) as conn: conn.execute("PRAGMA journal_mode=WAL")
    from src.services.daily_public_data_service import DailyPublicDataService
    original = DailyPublicDataService.decode_snapshot
    def concurrent(row):
        with ThreadPoolExecutor(1) as executor:
            executor.submit(seed, db, name="concurrent", observed="2026-09-03T10:00:00Z").result()
        return original(row)
    monkeypatch.setattr(DailyPublicDataService, "decode_snapshot", staticmethod(concurrent))
    assert WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)["snapshot"]["snapshot_id"] == "price-one"
    monkeypatch.setattr(DailyPublicDataService, "decode_snapshot", staticmethod(original))
    assert WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)["snapshot"]["snapshot_id"] == "concurrent"


def test_api_boundary_no_store_no_writes_and_unknown_symbol(api):
    response = api.get(f"/api/v2/research/wave-qualification/{SYMBOL}")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.json()["status"] == "insufficient_data"
    assert len(response.json()["checks"]) == 6 and all(c["status"] == "unknown" for c in response.json()["checks"])
    assert "note" not in response.json() and "content_fingerprint" not in response.json()
    assert api.get("/api/v2/research/wave-qualification/invalid").status_code == 422
    assert api.post(f"/api/v2/research/wave-qualification/{SYMBOL}", json={}).status_code in (403, 405)
    api.app.state.launch_handshake = None
    assert api.get(f"/api/v2/research/wave-qualification/{SYMBOL}").status_code == 503


def calendar_seed(conn, name, *, market="TPEX", revision=1, status="available", raw_id=None,
                  provider="tpex", ingested="2026-09-03T08:00:00Z", eligibility="eligible", logical="day1"):
    raw_id = raw_id or name
    conn.execute("""INSERT OR IGNORE INTO raw_resource_revisions
        (raw_resource_revision_id,identity_fingerprint,provider_id,resource_id,logical_revision_key,
         available_at,received_at,ingested_at,raw_payload_sha256,parser_version,schema_fingerprint,
         storage_policy,quality_status,eligibility_status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (raw_id, raw_id, provider, "fixture." + provider,
        logical, ingested, ingested, ingested, raw_id, "1", "a"*64, "hash_only", "fresh", eligibility))
    conn.execute("INSERT INTO trading_calendar_revisions VALUES(?,?,?,?,?,?,?,?,?,?)", (
        name, raw_id, market, "2026-09-01", "trading", ingested, ingested, revision, status, None))


def test_calendar_sql_market_revision_raw_supersession_and_cutoff(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        calendar_seed(conn, "other-exchange", market="TWSE", provider="twse")
        assert WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF) == []
        calendar_seed(conn, "accepted")
        result = WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF)
        assert len(result) == 1 and result[0]["raw_eligible"]
        calendar_seed(conn, "future", revision=4, ingested="2026-09-05T00:00:00Z")
        assert WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF)[0]["calendar_revision_id"] == "accepted"
        calendar_seed(conn, "revoked", revision=2, status="revoked", raw_id="accepted")
        assert WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF)[0]["status"] == "revoked"
        calendar_seed(conn, "replacement", revision=3, eligibility="ineligible", ingested="2026-09-03T09:00:00Z")
        latest = WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF)[0]
        assert latest["calendar_revision_id"] == "replacement" and not latest["raw_eligible"]


def test_old_calendar_does_not_resurrect_after_raw_revision_or_use_wrong_provider(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        calendar_seed(conn, "current")
        # Later source revision without a revised calendar cannot qualify the old proof.
        calendar_seed(conn, "unrelated", market="TWSE", ingested="2026-09-03T09:00:00Z")
        assert not WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF)[0]["raw_eligible"]
        calendar_seed(conn, "wrong-provider", revision=3, provider="twse", logical="other")
        assert not WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF)[0]["raw_eligible"]


def test_market_mismatch_and_malformed_content_never_pass(db, monkeypatch):
    seed(db)
    monkeypatch.setattr(UniverseRepository, "contexts_for_symbols_with_connection", lambda *a, **k:
        {SYMBOL: dict(identity_status="resolved", venue="TWSE", identity={})})
    assert states(WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF))["source"]["status"] == "failed"
    seed(db, name="broken-shape", observed="2026-09-03T09:00:00Z", mutate=lambda raw, data: data.update(excluded_rows=["bad"]))
    assert WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)["status"] == "unavailable"


def test_update_failure_and_historical_dates_remain_separate(db):
    seed(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO daily_public_attempts VALUES(?,?,?,?,?,?,?,?)", (
            "failed", "fixture-operation", SYMBOL, DATASET, "2026-09-03T09:00:00Z", "failed", "synthetic", None))
    result = WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)
    assert result["snapshot"]["snapshot_id"] == "price-one"
    assert any("更新未完成" in text for text in result["notices"])
    assert any("不能當作目前最新行情" in text for text in result["notices"])


def test_calendar_closed_conflict_and_date_only_unknown():
    life, ops = events()
    closed = [dict(row, calendar_revision_id="holiday-" + row["trade_date"], session_status="holiday") for row in calendars()]
    result = calendar_quality([], BOUNDS, calendars() + closed, life, ops, CUTOFF)
    assert result["status"] == "failed" and result["counts"]["conflicts"] == 3
    late = dict(life[0], effective_at=None, available_at="2026-09-01T00:00:00Z", event_date="2026-09-01")
    result = calendar_quality([dict(date=f"2026-09-0{d}") for d in (1, 2, 3)], BOUNDS, calendars(), [late], ops, CUTOFF)
    assert result["status"] == "unknown"


def test_shared_calendar_does_not_prove_exchange_open_and_revocation_has_reference(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        calendar_seed(conn, "shared-open", market="TW", provider="twse")
        assert not WaveQualificationService._calendars(conn, "TPEX", BOUNDS, CUTOFF)[0]["raw_eligible"]
    proofs = calendars()
    proofs[0]["status"] = "revoked"
    result = calendar_quality([], BOUNDS, proofs, [], [], CUTOFF)
    assert result["status"] == "unknown"
    assert dict(kind="calendar_revision", reference=proofs[0]["calendar_revision_id"]) in result["evidence"]
