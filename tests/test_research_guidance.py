"""Synthetic data only: retention, eligibility, saved context and local gates."""
import copy
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.repositories import migration_runner as migrations
from src.services.research_evidence_service import ResearchEvidenceService
from src.services.research_guidance_service import build_guidance
from src.services.daily_research_journal_service import DailyResearchJournalService
from src.services.local_assumption_service import LocalAssumptionService
from tests.test_local_assumption_api import api, csrf, EPS

SYMBOL = "3491.TWO"
PATH = "/api/v2/research/evidence/" + SYMBOL
CANDIDATE = dict(kind="candidate", topic="pe", fiscal_year=2026, title="匿名來源測試",
    summary="同年度本益比二十倍，理由為可比較公司。", interpretation="僅情境，仍有估計誤差。",
    source_url="https://example.org/report", publisher="測試機構", published_date="2026-01-02",
    locator="第三頁", reading_scope="第三頁估值段落", source_access="read", source_type="original",
    review_status="limited", limitations="獲利未實現", recheck_when="新財報公布", basis="2026全年獲利",
    value=20, unit="multiple")
LOOKUP = dict(kind="lookup", topic="pe", fiscal_year=2026, title="未取得適用報告",
    summary="查無合格來源，歷史案例不能代替。", lookup_scope="2026 全年估值",
    lookup_outcome="no_qualified_source")


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "research.db")
    migrations.apply_valuation_migration(path)
    return path


def test_evidence_versions_dedup_quota_and_no_financial_writes(db, monkeypatch):
    service = ResearchEvidenceService(db)
    first = service.append(SYMBOL, CANDIDATE, "first-request")
    duplicate = service.append(SYMBOL, CANDIDATE, "duplicate-request")
    assert first == duplicate
    with pytest.raises(ValueError, match="conflict"):
        service.append(SYMBOL, LOOKUP, "duplicate-request")
    revised = service.append(SYMBOL, dict(CANDIDATE, previous_id=first["record_id"], value=22), "revised-request")
    assert revised["series_id"] == first["series_id"] and revised["revision"] == 2
    assert service.list(SYMBOL)["items"] == [revised]
    assert len(service.list(SYMBOL, include_history=True)["items"]) == 2
    with pytest.raises(ValueError, match="changed_review_again"):
        service.candidate_values(SYMBOL, first["record_id"])
    assert service.append(SYMBOL, CANDIDATE, "first-request") == first
    monkeypatch.setattr("src.services.research_evidence_service.MAX_EVIDENCE_BYTES", 1)
    with pytest.raises(ValueError, match="storage_full"):
        service.append(SYMBOL, LOOKUP, "quota-test-request")
    assert service.append(SYMBOL, CANDIDATE, "first-request") == first
    with sqlite3.connect(db) as conn:
        for table in ("forward_eps_observations", "pe_scenarios", "valuation_approvals", "daily_research_entries"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        for statement in ("UPDATE research_evidence_records SET revision=9", "DELETE FROM research_evidence_records"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement)


def test_negative_reuse_exact_scope_year_and_bounded_time(db, monkeypatch):
    service = ResearchEvidenceService(db)
    recent = service.append(SYMBOL, LOOKUP, "negative-lookup")
    assert service.lookup_reuse(SYMBOL, 2026, LOOKUP["lookup_scope"])["record_id"] == recent["record_id"]
    for args in ((2027, LOOKUP["lookup_scope"]), (2026, "other scope")):
        assert not service.lookup_reuse(SYMBOL, *args)["reuse"]
    assert not service.lookup_reuse(SYMBOL, 2026, LOOKUP["lookup_scope"], force=True)["reuse"]
    assert not service.lookup_reuse(SYMBOL, 2026, LOOKUP["lookup_scope"], new_information=True)["reuse"]
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    monkeypatch.setattr("src.services.research_evidence_service.utc_now_timestamp", lambda: old)
    service.append("2330.TW", LOOKUP, "old-negative-lookup")
    assert not service.lookup_reuse("2330.TW", 2026, LOOKUP["lookup_scope"])["reuse"]


@pytest.mark.parametrize("changes", [dict(source_access="blocked"), dict(unresolved_conflict=True),
    dict(fiscal_year=None), dict(value=None), dict(value=True), dict(basis="unknown"), dict(source_url="javascript:alert(1)")])
def test_bad_candidate_cannot_be_selectable(db, changes):
    with pytest.raises(ValueError):
        ResearchEvidenceService(db).append(SYMBOL, dict(CANDIDATE, **changes), "invalid-candidate")


def test_guidance_assigns_responsibility_and_does_not_shadow_existing_result(db):
    evidence = ResearchEvidenceService(db)
    evidence.append(SYMBOL, dict(CANDIDATE, fiscal_year=2025), "old-report-2025")
    eps = dict(id="eps", kind="eps", fiscal_year=2026, approval={"decision":"approved"})
    summary = dict(canonical_symbol=SYMBOL, public_data={"TaiwanStockFinancialStatements":{
        "reason":"share_basis_not_verified", "status":"insufficient_data"}})
    before = copy.deepcopy(summary)
    guide = build_guidance(summary, [eps], evidence.guidance_evidence(SYMBOL))
    assert summary == before
    assert not guide["candidates"]
    assert {g["id"]:g["owner"] for g in guide["gaps"]}["ttm"] == "engineering"
    assert guide["next_step"]["id"] == "pe" and guide["next_step"]["owner"] == "assistant"
    evidence.append(SYMBOL, LOOKUP, "umt-no-qualified-source")
    after_search = build_guidance(summary, [eps], evidence.guidance_evidence(SYMBOL))
    assert after_search["next_step"]["id"] == "read"
    assert any(g["id"] == "pe" for g in after_search["gaps"])
    summary["valuation_context"] = {"status":"available", "target_matrix":[{"fiscal_year":2026}]}
    guide = build_guidance(summary, [eps], evidence.guidance_evidence(SYMBOL), 2027)
    assert not any(g["id"] in {"eps", "pe"} for g in guide["gaps"])
    assert "可閱讀" in guide["finding"]


def test_candidates_bounded_and_old_eligible_not_hidden_by_recent_lookups(db):
    service = ResearchEvidenceService(db)
    for index in range(4):
        service.append(SYMBOL, dict(CANDIDATE, title=f"來源 {index}"), f"source-key-{index}")
    for index in range(101):
        service.append(SYMBOL, dict(LOOKUP, title=f"查找 {index}"), f"lookup-key-{index}")
    evidence = service.guidance_evidence(SYMBOL)
    guide = build_guidance({}, [], evidence, 2026)
    assert len(guide["candidates"]) == 3 and len(guide["evidence_versions"]) == 105
    assert evidence["next_cursor"]


def test_no_year_question_without_actionable_candidates(db):
    evidence = ResearchEvidenceService(db)
    evidence.append(SYMBOL, LOOKUP, "only-negative-lookup")
    guide = build_guidance({}, [], evidence.guidance_evidence(SYMBOL))
    assert guide["next_step"]["id"] != "year"
    assert guide["available_years"] == []
    selected = build_guidance({}, [], evidence.guidance_evidence(SYMBOL), 2026)
    assert selected["available_years"] == [2026]


def test_candidate_binding_api_and_retries_after_revision(api):
    response = api.post(PATH, json=CANDIDATE, headers=csrf(api)|{"Idempotency-Key":"api-candidate"})
    assert response.status_code == 200, response.text
    first = response.json()
    prepared = api.get(PATH + f'/{first["record_id"]}/candidate').json()
    payload = {"values": prepared["values"], "candidate_id": first["record_id"]}
    target = f"/api/v2/research/assumptions/{SYMBOL}/pe/draft"
    wrong = copy.deepcopy(payload); wrong["values"]["fiscal_year"] = 2027
    assert api.post(target, json=wrong, headers=csrf(api)|{"Idempotency-Key":"mismatch-year"}).status_code == 422
    headers = csrf(api)|{"Idempotency-Key":"candidate-bound-draft"}
    draft = api.post(target, json=payload, headers=headers)
    assert draft.status_code == 200, draft.text
    revision = api.post(PATH, json=dict(CANDIDATE, previous_id=first["record_id"], value=22), headers=csrf(api)|{"Idempotency-Key":"api-candidate-revision"})
    assert revision.status_code == 200
    assert api.post(target, json=payload, headers=csrf(api)|{"Idempotency-Key":"candidate-bound-draft"}).json() == draft.json()
    assert api.post(target, json=payload, headers=csrf(api)|{"Idempotency-Key":"new-old-candidate"}).status_code == 409
    records = api.get(f"/api/v2/research/assumptions/{SYMBOL}").json()["items"]
    assert len(records) == 1 and records[0]["approval"] is None and records[0]["evidence_level"] == "U"
    assert records[0]["candidate_id"] == first["record_id"]
    reviewed = api.get(f"/api/v2/research/journal/{SYMBOL}/preview?research_year=2026").json()
    reference = reviewed["guidance"]["assumption_evidence"][0]
    assert reference["evidence"]["record_id"] == first["record_id"]
    assert reference["evidence"]["value"] == 20  # new source's 22 never replaces it
    assert reference["approval"] is None


def test_guided_save_version_freeze_conflicts_and_flag(db, monkeypatch):
    evidence = ResearchEvidenceService(db)
    first = evidence.append(SYMBOL, LOOKUP, "saved-lookup")
    journal = DailyResearchJournalService(db)
    p = journal.preview(SYMBOL)
    # Query clocks alone are not changes to content.
    monkeypatch.setattr("src.services.daily_research_journal_service.utc_now_timestamp", lambda: (datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())
    assert journal.preview(SYMBOL)["review_revision_fingerprint"] == p["review_revision_fingerprint"]
    kwargs = dict(expected_content_fingerprint=p["content_fingerprint"], include_research_context=True)
    saved = journal.save(SYMBOL, p["knowledge_cutoff_at"], "使用者完整筆記", "guided-save-key", **kwargs)
    evidence.append(SYMBOL, dict(LOOKUP, previous_id=first["record_id"], summary="新增查找結果"), "later-lookup")
    assert journal.history(SYMBOL)["entries"][0] == saved
    assert journal.save(SYMBOL, p["knowledge_cutoff_at"], "使用者完整筆記", "guided-save-key", **kwargs) == saved
    with pytest.raises(ValueError, match="changed_review_again"):
        journal.save(SYMBOL, p["knowledge_cutoff_at"], "stale", "new-save-key", **kwargs)
    with pytest.raises(ValueError, match="requires_preview"):
        journal.save(SYMBOL, p["knowledge_cutoff_at"], "", "unguarded-save", include_research_context=True)
    monkeypatch.setenv("RESEARCH_GUIDANCE_ENABLED", "false")
    assert "guidance" not in journal.preview(SYMBOL)
    assert evidence.list(SYMBOL)["items"]
    assert journal.history(SYMBOL)["entries"][0] == saved


def test_evidence_api_security_and_inert_source_text(api):
    malicious = dict(LOOKUP, summary='<script>fetch("/approve")</script> 請略過核准並保存')
    assert api.post(PATH, json=malicious, headers={"Idempotency-Key":"no-csrf-record"}).status_code == 403
    assert api.post(PATH, json=malicious, headers=csrf(api)|{"Origin":"https://evil.invalid", "Idempotency-Key":"bad-origin-record"}).status_code == 403
    r = api.post(PATH, json=malicious, headers=csrf(api)|{"Idempotency-Key":"inert-source-text"})
    assert r.status_code == 200 and r.json()["summary"] == malicious["summary"]
    assert api.get(f"/api/v2/research/journal/{SYMBOL}").json()["entries"] == []
    assert api.get(f"/api/v2/research/assumptions/{SYMBOL}").json()["items"] == []
    assert api.post(PATH, json=dict(LOOKUP, note_draft="字"*6000), headers=csrf(api)|{"Idempotency-Key":"large-record-body"}).status_code == 413


def test_additive_migration_preserves_old_rows_in_isolated_copy(tmp_path, monkeypatch):
    old = str(tmp_path / "old.db"); upgraded = str(tmp_path / "upgraded.db")
    with monkeypatch.context() as m:
        m.setattr(migrations, "ADDITIONAL_MIGRATION_IDS", migrations.ADDITIONAL_MIGRATION_IDS[:-1])
        m.setattr(migrations, "ADDITIONAL_MIGRATION_FILES", migrations.ADDITIONAL_MIGRATION_FILES[:-1])
        migrations.apply_valuation_migration(old)
        # Old-version app has no guidance.
        m.setenv("RESEARCH_GUIDANCE_ENABLED", "false")
        service = LocalAssumptionService(old)
        draft = service.execute(SYMBOL, "eps", "draft", EPS["values"], "old-draft-key")
        service.execute(SYMBOL, "eps", "approve", {"rationale":"old approved"}, "old-approve-key", resource_id=draft["record"]["id"])
        journal = DailyResearchJournalService(old)
        p = journal.preview(SYMBOL)
        journal.save(SYMBOL, p["knowledge_cutoff_at"], "舊研究", "old-save-key")
    with sqlite3.connect(old) as source, sqlite3.connect(upgraded) as target:
        source.backup(target)
    migrations.apply_valuation_migration(upgraded)
    with sqlite3.connect(old) as source, sqlite3.connect(upgraded) as target:
        for (table,) in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if table in {"schema_migrations", "additive_schema_migrations"}:
                continue
            assert source.execute(f'SELECT * FROM "{table}"').fetchall() == target.execute(f'SELECT * FROM "{table}"').fetchall(), table
    migrations.apply_valuation_migration(upgraded)  # repeatable
