import sqlite3

import pytest

import src.repositories.migration_runner as migration_runner
from src.repositories.migration_runner import MIGRATION_ID, apply_valuation_migration


def test_phase12_migration_is_additive_rerunnable_and_fresh_parent_safe(tmp_path):
    db = tmp_path / "missing" / "phase12.db"
    first = apply_valuation_migration(str(db))
    second = apply_valuation_migration(str(db))
    assert first["migration_id"] == "20260813_15_phase12_first_review_remediation"
    assert "20260813_14_evidence_model_v2_research_review_queue" in first["applied_migration_ids"]
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


def test_phase12_database_accepts_only_canonical_symbols(tmp_path):
    db = tmp_path / "canonical.db"
    apply_valuation_migration(str(db))
    with sqlite3.connect(db) as conn:
        for index, symbol in enumerate(("2330.TW", "2330.TWO")):
            conn.execute(
                "INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)",
                (f"valid-{index}", symbol, "active", "2026-08-13T00:00:00Z",
                 "2026-08-13T00:00:00Z", None, "research_review_queue_v1"),
            )
        for index, symbol in enumerate(("2330.tw", "2330", "ABC.TW", "23300.TW", "2330.TWXYZ")):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)",
                    (f"invalid-{index}", symbol, "active", "2026-08-13T00:00:00Z",
                     "2026-08-13T00:00:00Z", None, "research_review_queue_v1"),
                )


def test_phase12_reviewed_head_database_upgrades_without_checksum_rewrite(tmp_path, monkeypatch):
    db = tmp_path / "reviewed-head.db"
    with monkeypatch.context() as context:
        context.setattr(migration_runner, "MIGRATION_IDS", migration_runner.MIGRATION_IDS[:-1])
        context.setattr(migration_runner, "MIGRATION_FILES", migration_runner.MIGRATION_FILES[:-1])
        context.setattr(migration_runner, "MIGRATION_ID", migration_runner.MIGRATION_IDS[-2])
        apply_valuation_migration(str(db))
    result = apply_valuation_migration(str(db))
    assert result["applied_migration_ids"] == [
        "20260813_15_phase12_first_review_remediation"
    ]
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="symbol must be canonical"):
            conn.execute(
                "INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)",
                ("invalid", "2330.tw", "active", "2026-08-13T00:00:00Z",
                 "2026-08-13T00:00:00Z", None, "research_review_queue_v1"),
            )


def test_phase12_migration_rolls_back_as_one_transaction(tmp_path, monkeypatch):
    broken = tmp_path / "broken-phase12.sql"
    broken.write_text(
        "CREATE TABLE phase12_should_rollback(id TEXT);\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        migration_runner,
        "MIGRATION_FILES",
        (*migration_runner.MIGRATION_FILES[:-1], broken),
    )
    db = tmp_path / "rollback.db"
    with pytest.raises(sqlite3.Error):
        apply_valuation_migration(str(db))
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        triggers = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )}
    assert "phase12_should_rollback" not in tables
    assert "research_watchlist_items" not in tables
    assert "research_watchlist_symbol_canonical_insert" not in triggers
