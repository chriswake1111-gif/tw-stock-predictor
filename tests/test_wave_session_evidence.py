"""Anonymous fixed official-shaped responses; never use live egress in tests."""
from contextlib import closing
import json
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.collectors.installed_egress_client import validate_egress_url, EndpointNotAllowlistedError
from src.collectors.wave_session_sources import plan_requests, request_spec, parse_source, allowed_source_url
from src.repositories.wave_session_repository import WaveSessionRepository, connection
from src.services.wave_session_coverage import project_coverage
from src.services.wave_session_refresh import refresh
from src.services.wave_qualification_service import WaveQualificationService
from src.domain.valuation import normalize_utc_timestamp
from tests.test_research_guidance import db  # noqa: F401
from tests.test_local_assumption_api import api  # noqa: F401
from tests.test_wave_qualification import seed

SYMBOL = "9911.TWO"
BOUNDS = dict(start="2026-09-01", end="2026-09-03")
OBSERVED = "2026-09-04T01:00:00Z"
CUTOFF = "2026-09-05T01:00:00Z"
STOCK_FIELDS = ["日 期", "成交張數", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"]
HALT_FIELDS = ["編號", "有價證券類別", "有價證券代號", "有價證券名稱", "暫停交易日期", "暫停交易時間", "恢復交易日期", "恢復交易時間"]


def stock(days=(1, 2, 3)):
    rows = [[f"115/09/{day:02d}", "1", "100", "100", "102", "98", "101", "1", "10"] for day in days]
    return dict(stat="ok", date="20260901", code="9911", tables=[dict(date="20260901", fields=STOCK_FIELDS, data=rows, totalCount=len(rows))])


def halt(rows=()):
    return dict(stat="ok", date="2026", tables=[dict(fields=HALT_FIELDS, data=list(rows), totalCount=len(rows))])


def holiday(day=2):
    return dict(stat="ok", endDate="20260101", data=dict(html=f'<table><tr><td>中華民國115年有價證券櫃檯買賣市場開（休）市日期表</td></tr><tr><td>測試休市</td><td>9月{day}日</td><td>三</td><td>休市一天</td></tr></table>'))


def add(db, source="tpex_stock", payload=None, key="request-one", observed=OBSERVED, status="accepted", bounds=None):
    spec = request_spec(source, SYMBOL, **(bounds or BOUNDS))
    return WaveSessionRepository(db).append(spec, key=key, fetched_at=observed,
        raw_text=json.dumps(payload if payload is not None else stock(), ensure_ascii=False) if status == "accepted" else "",
        status=status, reason="test_source_unavailable" if status != "accepted" else "")


def projection(db, rows=None, cutoff=CUTOFF, bounds=None, lifecycle=()):
    with closing(connection(db)) as conn:
        conn.execute("BEGIN")
        return project_coverage(conn, SYMBOL, bounds or BOUNDS,
            rows if rows is not None else [dict(date="2026-09-01"), dict(date="2026-09-03")], cutoff, lifecycle=lifecycle)


def test_plan_is_exact_venue_bounded_and_allowlisted():
    for symbol in ("9911.TW", SYMBOL):
        specs = plan_requests(symbol, "2025-01-02", "2026-09-03")
        assert specs and len(specs) <= 120
        assert all(validate_egress_url(s["url"]) == s["url"] for s in specs)
        for spec in specs:
            for suffix in ("&url=https://evil.invalid", "&date=20260901", "#anything"):
                assert not allowed_source_url(spec["url"]+suffix)
                with pytest.raises(EndpointNotAllowlistedError): validate_egress_url(spec["url"]+suffix)
    for start, end in (("2020-01-01", "2026-09-03"), ("2026-09-03", "2026-09-01")):
        with pytest.raises(ValueError): plan_requests(SYMBOL, start, end)
    with pytest.raises(ValueError): request_spec("twse_stock", SYMBOL, **BOUNDS)


@pytest.mark.parametrize("change", ["symbol", "month", "count", "fields", "duplicate"])
def test_official_stock_schema_and_binding(change):
    payload = stock()
    if change == "symbol": payload["code"] = "9912"
    if change == "month": payload["date"] = "20260801"
    if change == "count": payload["tables"][0]["totalCount"] += 1
    if change == "fields": payload["tables"][0]["fields"] = STOCK_FIELDS[:-1]
    if change == "duplicate":
        payload["tables"][0]["data"][1] = payload["tables"][0]["data"][0]
    with pytest.raises(ValueError): parse_source(request_spec("tpex_stock", SYMBOL, **BOUNDS), payload)


def test_calendar_html_is_inert_and_incomplete_rows_not_invented():
    raw = holiday()
    raw["data"]["html"] += '<script>fetch("https://evil.invalid")</script><tr><td>9月3日</td><td>四</td><td>休市</td></tr>'
    result = parse_source(request_spec("tpex_holiday", SYMBOL, **BOUNDS), raw)
    assert result["facts"] == [dict(date="2026-09-02", kind="market_closed")]
    assert not result["absence_proves_normal"]


def test_calendar_selected_year_comes_from_title_not_latest_year():
    raw = holiday()
    raw["data"]["html"] = raw["data"]["html"].replace("115年", "114年")
    spec = request_spec("tpex_holiday", SYMBOL, "2025-09-01", "2025-09-03")
    assert parse_source(spec, raw)["facts"] == [dict(date="2025-09-02", kind="market_closed")]
    raw["data"]["html"] = raw["data"]["html"].replace("114年", "115年")
    with pytest.raises(ValueError, match="calendar_schema_changed"): parse_source(spec, raw)


def test_positive_volume_without_complete_prices_is_not_trade_evidence():
    raw = stock()
    raw["tables"][0]["data"][1][4] = "--"
    parsed = parse_source(request_spec("tpex_stock", SYMBOL, **BOUNDS), raw)
    assert [f["date"] for f in parsed["facts"]] == ["2026-09-01", "2026-09-03"]


def test_positive_trades_missing_and_empty_results_differ(db):
    add(db)
    result = projection(db)
    assert result["counts"]["missing"] == 1 and result["counts"]["stock_traded"] == 3
    assert result["historical_availability"] == "not_asserted"
    assert result["known_at"] == normalize_utc_timestamp(OBSERVED, "observed_at")
    add(db, payload=stock(()), key="request-empty", observed="2026-09-04T02:00:00Z")
    result = projection(db)
    assert result["counts"]["unknown"] == 3 and result["counts"]["stock_traded"] == 0
    assert result["status"] == "partial"


@pytest.mark.parametrize("status", ["failed", "revoked"])
def test_newer_overlap_blocks_older_proof_and_cutoff_preserved(db, status):
    old = add(db)
    add(db, key="new-request", status=status, observed="2026-09-04T02:00:00Z", bounds=dict(start="2026-09-02", end="2026-09-02"))
    current = projection(db)
    assert current["counts"]["unknown"] == 1 and current["counts"]["missing"] == 0
    before = projection(db, cutoff="2026-09-04T01:30:00Z")
    assert before["counts"]["missing"] == 1 and before["sources"][0]["reference"] == old["reference"]


def test_whole_day_halt_resumption_intraday_and_unpaired_stop(db):
    stop = [1, "上櫃股票", "9911", "匿名公司", "115/09/02", "8:00", "-", "-"]
    resume = [2, "上櫃股票", "9911", "匿名公司", "-", "-", "115/09/03", "8:00"]
    add(db, payload=stock((1, 3)))
    add(db, "tpex_halt", halt([stop, resume]), key="halts-one")
    assert projection(db)["counts"]["suspended"] == 1
    resume[-1] = "10:00"
    add(db, "tpex_halt", halt([stop, resume]), key="halts-two", observed="2026-09-04T02:00:00Z")
    assert projection(db)["counts"]["intraday"] == 1
    add(db, "tpex_halt", halt([stop]), key="halts-three", observed="2026-09-04T03:00:00Z")
    assert projection(db)["counts"]["suspended"] == 0
    assert projection(db)["counts"]["unknown"] == 1


@pytest.mark.parametrize("clock,expected", [("13:30", 0), ("13:30:01", 1)])
def test_closing_boundary_does_not_overstate_full_day(db, clock, expected):
    events = [[1, "上櫃股票", "9911", "匿名", "115/09/02", "8:00", "115/09/02", clock]]
    add(db, "tpex_halt", halt(events))
    assert projection(db, rows=[])["counts"]["suspended"] == expected


@pytest.mark.parametrize("uncertain_time", ["-", "8:00"])
def test_ambiguous_events_cannot_form_whole_day_exemption(db, uncertain_time):
    events = [
        [1, "上櫃股票", "9911", "匿名", "115/09/01", "8:00", "115/09/03", "8:00"],
        [2, "上櫃股票", "9911", "匿名", "115/09/02", uncertain_time, "115/09/02", uncertain_time],
    ]
    add(db, "tpex_halt", halt(events))
    result = projection(db, rows=[])
    assert result["counts"]["suspended"] == 0 and result["counts"]["intraday"] == 1


def test_future_trade_and_scheduled_resumption_do_not_prove_history(db):
    with pytest.raises(ValueError, match="future_trade_fact"):
        add(db, observed="2026-09-02T01:00:00Z")
    events = [[1, "上櫃股票", "9911", "匿名", "115/09/02", "8:00", "115/09/10", "8:00"]]
    add(db, "tpex_halt", halt(events))
    assert projection(db, rows=[])["counts"]["suspended"] == 0


def test_missing_samples_prioritized_over_long_unknown_prefix(db):
    bounds = dict(start="2026-09-01", end="2026-09-15")
    add(db, payload=stock((15,)), bounds=bounds, observed="2026-09-16T01:00:00Z")
    result = projection(db, rows=[], bounds=bounds, cutoff="2026-09-17T01:00:00Z")
    assert result["counts"]["unknown"] == 14 and result["samples"][0]["date"] == "2026-09-15"
    assert len(result["samples"]) <= 8


def test_holiday_conflict_and_listing_exclusion(db):
    add(db, "tpex_holiday", holiday(), key="holidays-one")
    assert projection(db)["counts"]["market_closed"] == 1
    add(db)
    assert projection(db)["counts"]["conflicts"] == 1
    life = [dict(event_type="listed", status="accepted", reason="initial_master_listing", event_date="2026-09-02")]
    assert projection(db, rows=[], lifecycle=life)["counts"]["conflicts"] == 2


def test_ledger_idempotency_immutability_quota_and_isolation(db, monkeypatch):
    before = {}
    with sqlite3.connect(db) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in {"wave_session_evidence", "sqlite_sequence"}]
        before = {t: conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
    first = add(db)
    assert add(db) == first
    with pytest.raises(ValueError, match="idempotency_conflict"): add(db, payload=stock((1,)))
    with sqlite3.connect(db) as conn:
        for statement in ("UPDATE wave_session_evidence SET fetched_at='bad'", "DELETE FROM wave_session_evidence"):
            with pytest.raises(sqlite3.IntegrityError): conn.execute(statement)
        assert all(conn.execute(f'SELECT * FROM "{t}"').fetchall() == rows for t, rows in before.items())
    monkeypatch.setattr("src.repositories.wave_session_repository.MAX_STORAGE_BYTES", 1)
    assert add(db) == first
    with pytest.raises(ValueError, match="storage_full"): add(db, key="request-quota")


def test_corrupt_latest_no_fallback_and_flag_rollback(db, monkeypatch):
    seed(db)
    add(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER wave_session_evidence_no_update")
        conn.execute("UPDATE wave_session_evidence SET content_sha256='broken'")
    assert WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)["status"] == "unavailable"
    monkeypatch.setenv("RESEARCH_WAVE_SESSION_EVIDENCE_ENABLED", "false")
    result = WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)
    assert result["status"] == "quality_warning" and "session_coverage" not in result


def test_read_snapshot_stays_consistent_during_append(db):
    with sqlite3.connect(db) as conn: conn.execute("PRAGMA journal_mode=WAL")
    add(db)
    with closing(connection(db)) as conn:
        conn.execute("BEGIN")
        one = project_coverage(conn, SYMBOL, BOUNDS, [], CUTOFF)
        with ThreadPoolExecutor(1) as pool:
            pool.submit(add, db, payload=stock(()), key="concurrent-new", observed="2026-09-04T02:00:00Z").result()
        two = project_coverage(conn, SYMBOL, BOUNDS, [], CUTOFF)
        assert one == two
    assert projection(db)["counts"]["unknown"] == 3


class OfflineClient:
    def __init__(self): self.calls = []
    def fetch(self, url, **kwargs):
        self.calls.append(url)
        payload = stock() if "tradingStock" in url else halt() if "sprcHis" in url else holiday()
        return 200, json.dumps(payload, ensure_ascii=False).encode(), {}


def test_preview_execute_repeat_and_bounded_failure(db):
    client = OfflineClient()
    params = dict(request_key="fixed-operation-1", client=client)
    before = open(db, "rb").read()
    preview = refresh(db, SYMBOL, **BOUNDS, **params)
    assert preview["request_count"] == 3 and not client.calls and open(db, "rb").read() == before
    result = refresh(db, SYMBOL, **BOUNDS, execute=True, **params)
    assert len(client.calls) == 3 and all(i["status"] == "accepted" for i in result["items"])
    refresh(db, SYMBOL, **BOUNDS, execute=True, **params)
    assert len(client.calls) == 3
    result = refresh(db, SYMBOL, **BOUNDS, execute=True, client=client, request_key="new-operation-key")
    assert len(client.calls) == 3 and all(i["status"] == "retained_recent" for i in result["items"])
    with sqlite3.connect(db) as conn: assert conn.execute("SELECT COUNT(*) FROM wave_session_evidence").fetchone()[0] == 3
    client.fetch = lambda *a, **k: (200, b'<script>bad()</script>', {})
    result = refresh(db, SYMBOL, **BOUNDS, execute=True, force=True, client=client, request_key="bad-operation-key")
    assert all(i["status"] == "failed" for i in result["items"])
    assert projection(db, cutoff="2026-10-01T00:00:00Z")["counts"]["unknown"] == 3
    with closing(connection(db)) as conn:
        records = WaveSessionRepository.read(conn, SYMBOL, **BOUNDS, cutoff="2026-10-01T00:00:00Z")
    assert all(r["response_sha256"] == hashlib.sha256(b'<script>bad()</script>').hexdigest() for r in records)
    assert all(r["http_status"] == 200 and r["raw_text"] == "" and r["normalized"] is None for r in records)


def test_retry_is_limited_to_selected_source(db):
    client = OfflineClient()
    result = refresh(db, SYMBOL, **BOUNDS, execute=True, client=client,
                     request_key="single-source-key", source_ids=["tpex_holiday"])
    assert len(client.calls) == 1 and result["request_count"] == 1
    assert "tradingDate" in client.calls[0]
    with pytest.raises(ValueError, match="source_scope_mismatch"):
        refresh(db, SYMBOL, **BOUNDS, request_key="wrong-market-key", source_ids=["twse_holiday"])


def test_no_create_no_auto_migrate_and_request_bounds(tmp_path):
    path = tmp_path/"absent.db"
    with pytest.raises(sqlite3.OperationalError): refresh(path, SYMBOL, **BOUNDS, request_key="test-missing")
    assert not path.exists()
    with sqlite3.connect(path) as conn: conn.execute("CREATE TABLE old_table (id INTEGER)")
    refresh(path, SYMBOL, **BOUNDS, request_key="old-preview")
    with pytest.raises(ValueError, match="migration_required"):
        refresh(path, SYMBOL, **BOUNDS, request_key="old-execute", execute=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("old_table",)]


def test_qualification_save_contract_separate_and_read_only(db):
    seed(db)
    add(db)
    before = open(db, "rb").read()
    result = WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)
    assert result["session_coverage"]["counts"]["stock_traded"] == 3
    assert result["automatic_candidates_eligible"] is False
    assert "confirmation_token" not in result and "content_fingerprint" not in result
    assert open(db, "rb").read() == before


def test_new_positive_evidence_cannot_erase_existing_failed_state(db, monkeypatch):
    from src.services.wave_qualification_service import check
    seed(db)
    add(db)
    failure = check("sessions", "交易日與交易狀態", "failed", "existing_state_conflict",
                    "既有交易狀態與行情衝突。", "保留原證據。",
                    evidence=[dict(kind="operational_event_id", reference="original-event")])
    monkeypatch.setattr("src.services.wave_qualification_service.calendar_quality", lambda *a: failure)
    result = WaveQualificationService(db).get(SYMBOL, cutoff=CUTOFF)
    assert result["session_coverage"]["counts"]["stock_traded"] == 3
    assert next(c for c in result["checks"] if c["id"] == "sessions") == failure


def test_api_coverage_read_has_no_network_or_storage_side_effects(api, monkeypatch):
    import os
    path = os.environ["DATABASE_PATH"]
    seed(path)
    add(path)
    before = open(path, "rb").read()
    def forbidden(*args, **kwargs):
        raise AssertionError("reading must not collect sources")
    monkeypatch.setattr("src.collectors.installed_egress_client.InstalledEgressClient.fetch", forbidden)
    response = api.get(f"/api/v2/research/wave-qualification/{SYMBOL}")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["session_coverage"]["requested_range"] == payload["requested_range"] == BOUNDS
    assert payload["automatic_candidates_eligible"] is False
    assert "raw_text" not in response.text and "confirmation_token" not in response.text
    assert open(path, "rb").read() == before


def test_backup_restore_preserves_independent_evidence(db, tmp_path):
    from src.services.evidence_backup_service import EvidenceBackupService
    record = add(db)
    backup, restored = tmp_path/"evidence-backup.db", tmp_path/"evidence-restored.db"
    saved = EvidenceBackupService.backup(str(db), str(backup))
    restored_result = EvidenceBackupService.restore(str(backup), str(restored))
    assert saved["irreplaceable_counts"]["wave_session_evidence"] == 1
    assert restored_result["status"] == "valid"
    assert WaveSessionRepository(restored).by_request("request-one") == record
