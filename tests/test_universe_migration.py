from __future__ import annotations

import sqlite3

from src.repositories.migration_runner import MIGRATION_ID, apply_valuation_migration


def test_phase13_migration_is_fresh_parent_safe_rerunnable_and_seeded(tmp_path):
    db = tmp_path / "missing" / "nested" / "universe.sqlite"
    first = apply_valuation_migration(str(db))
    second = apply_valuation_migration(str(db))
    assert db.exists()
    assert first["migration_id"] == MIGRATION_ID == "20260821_16_phase13_universe_foundation"
    assert first["applied"] is True
    assert second["applied"] is False
    with sqlite3.connect(db) as conn:
        resources = {row[0] for row in conn.execute("SELECT logical_resource_key FROM data_resources")}
        assert "tpex_mainboard_quotes" not in resources
        assert conn.execute("SELECT COUNT(*) FROM universe_resource_policies").fetchone()[0] == 7
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_phase13_immutable_anchor_and_idempotency_triggers(tmp_path):
    db = tmp_path / "db.sqlite"
    apply_valuation_migration(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO universe_instruments VALUES (?,?,?,?,?,?,?,?,?,?)", ("u1", "TWSE", "2330", 1, "a"*64, "2026-08-21T00:00:00Z", "fixture", "source", None, "2026-08-21T00:00:00Z"))
        with __import__("pytest").raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM universe_instruments WHERE instrument_id='u1'")
