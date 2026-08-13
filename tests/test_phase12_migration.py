import sqlite3

import pytest

from src.repositories.migration_runner import MIGRATION_ID, apply_valuation_migration


def test_phase12_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db = tmp_path / "missing" / "phase12.db"
    first = apply_valuation_migration(str(db))
    second = apply_valuation_migration(str(db))
    assert first["migration_id"] == "20260813_14_evidence_model_v2_research_review_queue"
    assert MIGRATION_ID == first["migration_id"]
    assert second["applied"] is False
    with sqlite3.connect(db) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"research_watchlist_items", "research_review_events"} <= names


def test_phase12_database_guards_watchlist_and_review_history(tmp_path):
    db = tmp_path / "phase12.db"
    apply_valuation_migration(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)",
            ("item-1", "2330.TW", "active", "2026-08-13T00:00:00Z",
             "2026-08-13T00:00:00Z", None, "research_review_queue_v1"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM research_watchlist_items WHERE watchlist_item_id='item-1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE research_watchlist_items SET symbol='2317.TW'")
