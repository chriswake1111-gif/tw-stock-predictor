"""Phase 20 WP01: Tests for Migration 22, Universe v2 Normalization, and BC-IMP-1 child revision numbering."""

from __future__ import annotations

import hashlib
import sqlite3
import pytest

from src.collectors.universe_collectors import parse_universe_payload
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import (
    UniverseRepository,
    UniverseIngestionRepository,
)
from src.services.universe_write_guard import (
    UniverseOperatorContext,
    UniverseWriteGuard,
)
from tests.phase13_test_support import seed_raw_provenance


def _context(suffix="p20"):
    return UniverseOperatorContext("operator", f"run-{suffix}", f"lock-{suffix}", f"audit-{suffix}")


def _seed_raw(db_path, raw_id: str, resource_id: str, raw_bytes: bytes = b"test") -> str:
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    schema_hash = hashlib.sha256(f"schema:{resource_id}".encode()).hexdigest()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO raw_resource_revisions VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                raw_id, f"identity-{raw_id}", "twse-universe-official", resource_id, "fixture",
                "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z",
                "2026-08-21T00:00:00Z", "2026-08-21T00:00:00Z", raw_hash,
                "1", schema_hash, "hash_only", None, "fresh", "eligible", None,
                "phase20 fixture provenance",
            ),
        )
    return raw_hash


def _setup_db(tmp_path):
    db = tmp_path / "phase20_universe.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    guard = UniverseWriteGuard(enabled=True)
    repo = UniverseRepository(str(db), guard=guard)
    ingestion = UniverseIngestionRepository(str(db), guard=guard)
    return db, repo, ingestion, guard


def test_migration22_adds_short_name_and_preserves_triggers(tmp_path):
    db, repo, ingestion, _ = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(universe_instrument_revisions)").fetchall()}
        assert "short_name" in cols
        indices = {row[1] for row in conn.execute("PRAGMA index_list(universe_instrument_revisions)").fetchall()}
        assert "idx_universe_instrument_revisions_short_name" in indices
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # Verify immutability triggers on universe_instruments
        conn.execute(
            "INSERT INTO universe_instruments VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("u_test", "TWSE", "2330", 1, "a" * 64, "2026-09-01T00:00:00Z", "fix", "src", None, "2026-09-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE universe_instruments SET official_code='2331' WHERE instrument_id='u_test'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM universe_instruments WHERE instrument_id='u_test'")


def test_universe_v2_parser_extracts_twse_and_tpex_short_name():
    # TWSE: 公司簡稱
    twse_payload = [
        {
            "出表日期": "1150901",
            "公司代號": "2330",
            "公司名稱": "台灣積體電路製造股份有限公司",
            "公司簡稱": "台積電",
        }
    ]
    twse_rows = parse_universe_payload("twse.t187ap03_L", twse_payload)
    assert len(twse_rows) == 1
    assert twse_rows[0]["official_code"] == "2330"
    assert twse_rows[0]["short_name"] == "台積電"

    # TPEx: CompanyAbbreviation
    tpex_payload = [
        {
            "Date": "20260901",
            "SecuritiesCompanyCode": "8069",
            "CompanyName": "元太科技工業股份有限公司",
            "CompanyAbbreviation": "元太",
        }
    ]
    tpex_rows = parse_universe_payload("tpex.mopsfin_t187ap03_O", tpex_payload)
    assert len(tpex_rows) == 1
    assert tpex_rows[0]["official_code"] == "8069"
    assert tpex_rows[0]["short_name"] == "元太"


def test_bc_imp_1_child_revision_numbering_repository_authoritative(tmp_path):
    """Prove BC-IMP-1:

    1. Resource revision 2 with instrument max revision > 2 succeeds without collision;
    2. Caller cannot influence child revision numbering;
    3. Multiple resource histories for one instrument remain collision-safe;
    4. Idempotent replay does not allocate a new child revision;
    5. Supersession lineage remains correct.
    """
    db, repo, ingestion, _ = _setup_db(tmp_path)
    ctx = _context("bc_imp_1")
    raw_hash_master = _seed_raw(db, "raw-phase13-twse_universe_master", "twse-universe-master", b"raw-v2-master")
    raw_hash_newlisting = _seed_raw(db, "raw-phase13-twse_newlisting", "twse-universe-newlisting", b"raw-v2-newlisting")

    # Create anchor instrument
    anchor = repo.allocate_instrument(
        venue="TWSE",
        official_code="2330",
        source_identity="twse:2330",
        first_observed_at="2026-09-01T00:00:00Z",
        source_reference="fixture",
        context=ctx,
    )
    inst_id = anchor["instrument_id"]

    # Ingest revision 1 from resource A (twse-universe-master)
    r1 = repo.add_revision(
        context=ctx,
        idempotency_key="univ-twse-v2-2330-raw1",
        instrument_id=inst_id,
        resource_id="twse-universe-master",
        logical_revision_key="twse:2330:master",
        revision_number=1,
        payload={
            "venue": "TWSE",
            "official_code": "2330",
            "canonical_symbol": "2330.TW",
            "display_name": "台積電",
            "short_name": "台積電",
            "security_type": "股票",
            "fetched_at": "2026-09-01T00:00:00Z",
            "received_at": "2026-09-01T00:00:00Z",
            "ingested_at": "2026-09-01T00:00:00Z",
            "available_at": "2026-09-01T00:00:00Z",
            "source_reference": "twse.t187ap03_L",
            "status": "accepted",
            "freshness_status": "current",
            "freshness_mode": "official_cadence_window",
            "current_complete": True,
            "coverage_complete": True,
            "parser_version": "2.0.0",
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash_master,
        },
    )
    assert r1["created"] is True
    assert r1["revision_number"] == 1

    with sqlite3.connect(db) as conn:
        uir1 = conn.execute(
            "SELECT * FROM universe_instrument_revisions WHERE instrument_id = ? ORDER BY revision_number DESC LIMIT 1",
            (inst_id,),
        ).fetchone()
        assert uir1[4] == 1  # revision_number is index 4
        assert uir1[-1] == "台積電"  # short_name column is last

    # 4. Idempotent replay does not allocate a new child revision
    r1_replay = repo.add_revision(
        context=ctx,
        idempotency_key="univ-twse-v2-2330-raw1",
        instrument_id=inst_id,
        resource_id="twse-universe-master",
        logical_revision_key="twse:2330:master",
        revision_number=1,
        payload={
            "venue": "TWSE",
            "official_code": "2330",
            "canonical_symbol": "2330.TW",
            "display_name": "台積電",
            "short_name": "台積電",
            "security_type": "股票",
            "fetched_at": "2026-09-01T00:00:00Z",
            "received_at": "2026-09-01T00:00:00Z",
            "ingested_at": "2026-09-01T00:00:00Z",
            "available_at": "2026-09-01T00:00:00Z",
            "source_reference": "twse.t187ap03_L",
            "status": "accepted",
            "freshness_status": "current",
            "freshness_mode": "official_cadence_window",
            "current_complete": True,
            "coverage_complete": True,
            "parser_version": "2.0.0",
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash_master,
        },
    )
    assert r1_replay["idempotent"] is True
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM universe_instrument_revisions WHERE instrument_id = ?", (inst_id,)).fetchone()[0]
        assert count == 1

    # Ingest from another corroborating resource (e.g. twse-newlisting) for the SAME instrument
    # so instrument's max revision advances to 2, 3...
    r_other = repo.add_revision(
        context=ctx,
        idempotency_key="univ-other-2330-1",
        instrument_id=inst_id,
        resource_id="twse-universe-newlisting",
        logical_revision_key="twse:2330:newlisting",
        revision_number=1,
        payload={
            "venue": "TWSE",
            "official_code": "2330",
            "canonical_symbol": "2330.TW",
            "display_name": "台積電",
            "security_type": "股票",
            "fetched_at": "2026-09-01T00:00:00Z",
            "received_at": "2026-09-01T00:00:00Z",
            "ingested_at": "2026-09-01T00:00:00Z",
            "available_at": "2026-09-01T00:00:00Z",
            "source_reference": "twse.company.newlisting",
            "status": "accepted",
            "freshness_status": "current",
            "freshness_mode": "official_cadence_window",
            "current_complete": True,
            "coverage_complete": True,
            "raw_resource_revision_id": "raw-phase13-twse_newlisting",
            "raw_payload_sha256": raw_hash_newlisting,
        },
    )
    assert r_other["revision_number"] == 1
    with sqlite3.connect(db) as conn:
        # Check child revision number for r_other: it must be 2!
        uir2 = conn.execute(
            "SELECT revision_number FROM universe_instrument_revisions WHERE universe_revision_id = ?",
            (r_other["universe_revision_id"],),
        ).fetchone()
        assert uir2[0] == 2

    # Now, ingest resource A revision 2! Resource A's logical chain is at revision 2,
    # but instrument's max child revision is ALREADY 2.
    # Therefore, child revision number must become 3 without UNIQUE(instrument_id, revision_number) collision!
    r2 = repo.add_revision(
        context=ctx,
        idempotency_key="univ-twse-v2-2330-raw2",
        instrument_id=inst_id,
        resource_id="twse-universe-master",
        logical_revision_key="twse:2330:master",
        revision_number=2,
        payload={
            "venue": "TWSE",
            "official_code": "2330",
            "canonical_symbol": "2330.TW",
            "display_name": "台積電股份有限公司",
            "short_name": "台積電",
            "security_type": "股票",
            "fetched_at": "2026-09-02T00:00:00Z",
            "received_at": "2026-09-02T00:00:00Z",
            "ingested_at": "2026-09-02T00:00:00Z",
            "available_at": "2026-09-02T00:00:00Z",
            "source_reference": "twse.t187ap03_L",
            "status": "accepted",
            "freshness_status": "current",
            "freshness_mode": "official_cadence_window",
            "current_complete": True,
            "coverage_complete": True,
            "parser_version": "2.0.0",
            "supersedes_revision_id": r1["universe_revision_id"],
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash_master,
        },
    )
    assert r2["created"] is True
    assert r2["revision_number"] == 2
    assert r2["supersedes_revision_id"] == r1["universe_revision_id"]

    with sqlite3.connect(db) as conn:
        uir3 = conn.execute(
            "SELECT revision_number, supersedes_revision_id, short_name FROM universe_instrument_revisions WHERE universe_revision_id = ?",
            (r2["universe_revision_id"],),
        ).fetchone()
        assert uir3[0] == 3  # Derived transactionally as 3!
        assert uir3[1] == uir1[0]  # Points to previous child revision ID!
        assert uir3[2] == "台積電"


def test_sc_15_phase19_upgrade_dual_supersession(tmp_path):
    """SC-15: Upgrading Phase 19 DB to Phase 20 maintains dual supersession and allows ingesting v2 revisions."""
    db, repo, ingestion, _ = _setup_db(tmp_path)
    ctx = _context("sc_15")
    raw_hash_v1 = _seed_raw(db, "raw-phase13-twse_universe_master_v1", "twse-universe-master", b"raw-v1")
    raw_hash_v2 = _seed_raw(db, "raw-phase13-twse_universe_master_v2", "twse-universe-master", b"raw-v2")

    anchor = repo.allocate_instrument(
        venue="TWSE",
        official_code="2454",
        source_identity="twse:2454",
        first_observed_at="2026-08-20T00:00:00Z",
        source_reference="fixture",
        context=ctx,
    )
    inst_id = anchor["instrument_id"]

    # Phase 19 revision (revision 1, short_name=None, parser_version=1)
    p19_rev = repo.add_revision(
        context=ctx,
        idempotency_key="univ-twse-2454-raw1",
        instrument_id=inst_id,
        resource_id="twse-universe-master",
        logical_revision_key="twse:2454:master",
        revision_number=1,
        payload={
            "venue": "TWSE",
            "official_code": "2454",
            "canonical_symbol": "2454.TW",
            "display_name": "聯發科技股份有限公司",
            "security_type": "股票",
            "fetched_at": "2026-08-20T00:00:00Z",
            "received_at": "2026-08-20T00:00:00Z",
            "ingested_at": "2026-08-20T00:00:00Z",
            "available_at": "2026-08-20T00:00:00Z",
            "source_reference": "twse.t187ap03_L",
            "status": "accepted",
            "freshness_status": "current",
            "freshness_mode": "official_cadence_window",
            "current_complete": True,
            "coverage_complete": True,
            "parser_version": "1",
            "raw_resource_revision_id": "raw-phase13-twse_universe_master_v1",
            "raw_payload_sha256": raw_hash_v1,
        },
    )

    # Now Phase 20 sync materializes v2 with short_name = "聯發科"
    p20_rev = repo.add_revision(
        context=ctx,
        idempotency_key="univ-twse-v2-2454-raw2",
        instrument_id=inst_id,
        resource_id="twse-universe-master",
        logical_revision_key="twse:2454:master",
        revision_number=2,
        payload={
            "venue": "TWSE",
            "official_code": "2454",
            "canonical_symbol": "2454.TW",
            "display_name": "聯發科技股份有限公司",
            "short_name": "聯發科",
            "security_type": "股票",
            "fetched_at": "2026-09-05T00:00:00Z",
            "received_at": "2026-09-05T00:00:00Z",
            "ingested_at": "2026-09-05T00:00:00Z",
            "available_at": "2026-09-05T00:00:00Z",
            "source_reference": "twse.t187ap03_L",
            "status": "accepted",
            "freshness_status": "current",
            "freshness_mode": "official_cadence_window",
            "current_complete": True,
            "coverage_complete": True,
            "parser_version": "2.0.0",
            "supersedes_revision_id": p19_rev["universe_revision_id"],
            "raw_resource_revision_id": "raw-phase13-twse_universe_master_v2",
            "raw_payload_sha256": raw_hash_v2,
        },
    )
    assert p20_rev["created"] is True
    assert p20_rev["revision_number"] == 2
    assert p20_rev["supersedes_revision_id"] == p19_rev["universe_revision_id"]

    with sqlite3.connect(db) as conn:
        parent_uir = conn.execute(
            "SELECT instrument_revision_id FROM universe_instrument_revisions WHERE universe_revision_id = ?",
            (p19_rev["universe_revision_id"],),
        ).fetchone()[0]
        child_uir = conn.execute(
            "SELECT instrument_revision_id, supersedes_revision_id, revision_number, short_name, parser_version FROM universe_instrument_revisions WHERE universe_revision_id = ?",
            (p20_rev["universe_revision_id"],),
        ).fetchone()
        assert child_uir[1] == parent_uir
        assert child_uir[2] == 2
        assert child_uir[3] == "聯發科"
        assert child_uir[4] == "2.0.0"
