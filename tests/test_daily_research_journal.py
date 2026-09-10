import sqlite3
from types import SimpleNamespace

import pytest

from src.repositories.migration_runner import apply_valuation_migration
from src.services.daily_research_journal_service import (
    DailyResearchJournalService,
    compare_entries,
)


def summary(symbol="2330.TW", *, day="2026-09-10", close=2465, pe=25.9, assumptions=None):
    return {
        "canonical_symbol": symbol, "status": "partial", "venue": "TWSE",
        "market_context": {"official_close": close, "settled_trade_date": day},
        "public_data": {
            "TaiwanStockPER": {"source": "FinMind", "rows": [{"date": day, "pe": pe, "pb": 8.2, "yield_ratio": .01}]},
            "TaiwanStockPrice": {"source": "FinMind", "rows": [{"date": day, "volume": 100}]},
        },
        "valuation_context": assumptions or {"id": "v1"}, "technical_context": {"id": "t1"},
    }


@pytest.fixture
def journal(tmp_path):
    db = str(tmp_path / "journal.db")
    apply_valuation_migration(db)
    svc = DailyResearchJournalService(db)
    svc.summary_service = SimpleNamespace(get_summary=lambda symbol, knowledge_cutoff_at: summary(symbol))
    return svc, db


def test_partial_or_none_summary_is_saved_and_plain_note_survives_restart(journal):
    svc, db = journal
    svc.summary_service = SimpleNamespace(get_summary=lambda symbol, knowledge_cutoff_at: None)
    saved = svc.save("2330.TW", "2026-09-10T00:00:00Z", "研究備註", "journal-none-1")
    assert saved["status"] == "partial" and saved["summary"]["status"] == "insufficient_data"
    restarted = DailyResearchJournalService(db)
    entry = restarted.history("2330.TW")["entries"][0]
    assert entry["note"] == "研究備註"
    assert entry["historical_eligibility"] == "not_asserted"


def test_same_key_retry_does_not_read_source_and_different_payload_conflicts(journal):
    svc, _ = journal
    calls = []
    svc.summary_service = SimpleNamespace(get_summary=lambda *args, **kwargs: calls.append(1) or summary())
    first = svc.save("2330.TW", "2026-09-10T00:00:00Z", "a", "journal-retry-1")
    second = svc.save("2330.TW", "2026-09-10T00:00:00Z", "a", "journal-retry-1")
    assert first == second and len(calls) == 1
    with pytest.raises(ValueError, match="idempotency_conflict"):
        svc.save("2330.TW", "2026-09-10T00:00:00Z", "b", "journal-retry-1")


def test_future_cutoff_rejected(journal):
    svc, _ = journal
    with pytest.raises(ValueError, match="future_knowledge_cutoff"):
        svc.save("2330.TW", "2999-01-01T00:00:00Z", "x", "future-cutoff-1")


def test_comparison_requires_same_source_date_direction_and_non_none_values():
    old = {"summary": summary(day="2026-09-09", close=2400), "assumption_fingerprint": "a"}
    current = {"summary": summary(day="2026-09-10", close=2465), "assumption_fingerprint": "a"}
    result = compare_entries(old, current)
    close = next(item for item in result["facts"] if item["field"] == "official_close")
    assert close["status"] == "comparable" and close["delta"] == 65
    changed_provider = {"summary": {**current["summary"], "public_data": {**current["summary"]["public_data"], "TaiwanStockPER": {**current["summary"]["public_data"]["TaiwanStockPER"], "source": "Other"}}}, "assumption_fingerprint": "a"}
    assert all(item["status"] == "not_comparable" for item in compare_entries(old, changed_provider)["facts"] if item["field"] in {"pe", "pb", "yield_ratio"})
    backward = {"summary": summary(day="2026-09-08"), "assumption_fingerprint": "a"}
    assert next(item for item in compare_entries(old, backward)["facts"] if item["field"] == "pe")["status"] == "not_comparable"
    none_current = {"summary": summary(), "assumption_fingerprint": "a"}
    none_current["summary"]["public_data"]["TaiwanStockPER"]["rows"][0]["pe"] = None
    assert next(item for item in compare_entries(old, none_current)["facts"] if item["field"] == "pe")["status"] == "not_comparable"


def test_assumptions_changed_is_independent_flag(journal):
    svc, _ = journal
    svc.summary_service = SimpleNamespace(get_summary=lambda symbol, knowledge_cutoff_at: summary(assumptions={"id": "changed"}))
    first = svc.save("2330.TW", "2026-09-09T00:00:00Z", "a", "assume-change-1")
    svc.summary_service = SimpleNamespace(get_summary=lambda symbol, knowledge_cutoff_at: summary(assumptions={"id": "new"}))
    svc.save("2330.TW", "2026-09-10T00:00:00Z", "b", "assume-change-2")
    result = svc.history("2330.TW")["comparison"]
    assert result["assumptions_changed"] is True
    assert result["model_comparison_status"] == "assumptions_changed"


def test_overview_has_server_time_and_watchlist_dates(journal):
    svc, db = journal
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)", ("w1", "2330.TW", "active", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", None, "research_review_queue_v1"))
        conn.commit()
    result = svc.overview()
    assert result["server_time"].endswith("Z")
    assert result["items"][0]["symbol"] == "2330.TW"
    assert result["items"][0]["current"]["knowledge_cutoff_at"].endswith("Z")


def test_hash_mismatch_fails_closed(journal):
    svc, db = journal
    svc.save("2330.TW", "2026-09-10T00:00:00Z", "x", "hash-fail-1")
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER daily_research_entry_no_update")
        conn.execute("UPDATE daily_research_entries SET payload_sha256='bad' WHERE idempotency_key='hash-fail-1'")
        conn.commit()
    with pytest.raises(Exception, match="daily_research_integrity_error"):
        svc.history("2330.TW")
