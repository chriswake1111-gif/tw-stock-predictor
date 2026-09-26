import sqlite3
from types import SimpleNamespace

import pytest

from src.repositories.migration_runner import apply_valuation_migration
from src.services.daily_research_journal_service import DailyResearchJournalService


@pytest.fixture
def service(tmp_path):
    db = str(tmp_path / "review.db")
    apply_valuation_migration(db)
    svc = DailyResearchJournalService(db)
    state = {"close":100, "approval":"a", "source":"official", "pe_year":2026, "policy":"same_fiscal_year_v1"}
    def summary(symbol, knowledge_cutoff_at):
        return {"canonical_symbol":symbol, "knowledge_cutoff_at":knowledge_cutoff_at,
                "venue":state["source"], "market_context":{"official_close":state["close"], "settled_trade_date":"2026-09-01"},
                "valuation_context":{"pe_fiscal_year":state["pe_year"], "pairing_policy_version":state["policy"]},
                "technical_context":{"targets":{"knowledge_cutoff_at":knowledge_cutoff_at,
                    "rule_trace":{"source_data_as_of":knowledge_cutoff_at, "approval_id":state["approval"]}}}}
    svc.summary_service = SimpleNamespace(get_summary=summary)
    return svc, state


def test_preview_no_watchlist_or_journal_writes(service):
    svc, _ = service
    preview = svc.preview("2330.TW")
    assert preview["comparison"]["status"] == "no_previous"
    assert len(preview["content_fingerprint"]) == 64
    with sqlite3.connect(svc.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_research_entries").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM research_watchlist_items").fetchone()[0] == 0


def test_guarded_save_same_content_and_retry_survive_clock_change(service, monkeypatch):
    svc, state = service
    monkeypatch.setattr("src.services.daily_research_journal_service.utc_now_timestamp", lambda:"2026-09-10T00:00:00Z")
    p = svc.preview("2330.TW")
    monkeypatch.setattr("src.services.daily_research_journal_service.utc_now_timestamp", lambda:"2026-09-10T00:01:00Z")
    first = svc.save("2330.TW", p["knowledge_cutoff_at"], "AI 草稿：待複核", "save-confirmed-1", p["content_fingerprint"])
    assert first["summary"] == p["current"]["summary"]
    state["close"] = 101
    assert svc.save("2330.TW", p["knowledge_cutoff_at"], "AI 草稿：待複核", "save-confirmed-1", p["content_fingerprint"]) == first
    with pytest.raises(ValueError, match="idempotency_conflict"):
        svc.save("2330.TW", p["knowledge_cutoff_at"], "不同筆記", "save-confirmed-1", p["content_fingerprint"])


@pytest.mark.parametrize("field,value", [("close",101), ("approval","revoked"), ("source","revised_source"), ("pe_year",2027), ("policy","new_policy")])
def test_change_between_preview_and_save_rejected_without_insert(service, field, value):
    svc, state = service
    p = svc.preview("2330.TW")
    state[field] = value
    with pytest.raises(ValueError, match="content_changed"):
        svc.save("2330.TW", p["knowledge_cutoff_at"], "note", "reject-changes", p["content_fingerprint"])
    assert svc.history("2330.TW")["entries"] == []


def test_data_newer_than_review_cutoff_also_invalidates_confirmation(service, monkeypatch):
    svc, _ = service
    old = svc.summary_service.get_summary
    monkeypatch.setattr("src.services.daily_research_journal_service.utc_now_timestamp", lambda:"2026-09-10T00:00:00Z")
    p = svc.preview("2330.TW")
    def changed(symbol, knowledge_cutoff_at):
        value = old(symbol, knowledge_cutoff_at)
        if knowledge_cutoff_at > p["knowledge_cutoff_at"]:
            value["market_context"]["official_close"] = 105
        return value
    svc.summary_service.get_summary = changed
    monkeypatch.setattr("src.services.daily_research_journal_service.utc_now_timestamp", lambda:"2026-09-10T00:02:00Z")
    with pytest.raises(ValueError, match="content_changed"):
        svc.save("2330.TW", p["knowledge_cutoff_at"], "note", "late-ingestion", p["content_fingerprint"])


def test_previous_saved_research_changed_requires_new_review(service):
    svc, _ = service
    p = svc.preview("2330.TW")
    svc.save("2330.TW", p["knowledge_cutoff_at"], "另一份", "another-research")
    with pytest.raises(ValueError, match="content_changed"):
        svc.save("2330.TW", p["knowledge_cutoff_at"], "note", "previous-changed", p["content_fingerprint"])
