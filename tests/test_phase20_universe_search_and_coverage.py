"""Phase 20 WP02: Tests for Effective Master Coverage & Local Search API (BC-IP-2)."""

from __future__ import annotations

import hashlib
import sqlite3
import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.repositories.migration_runner import apply_valuation_migration
from src.repositories.universe_repository import (
    UniverseRepository,
)
from src.runtime.settings import RuntimePaths, RuntimeSettings
from src.services.universe_write_guard import (
    UniverseOperatorContext,
    UniverseWriteGuard,
)
from tests.phase13_test_support import seed_raw_provenance


def _context(suffix="p20_search"):
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
    db = tmp_path / "phase20_search.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    guard = UniverseWriteGuard(enabled=True)
    repo = UniverseRepository(str(db), guard=guard)
    return db, repo, guard


def test_coverage_not_initialized(tmp_path):
    db, repo, _ = _setup_db(tmp_path)
    coverage = repo.get_short_name_coverage()
    assert coverage["universe_status"] == "not_initialized"
    assert coverage["total_instruments"] == 0
    assert coverage["phase20_materialized_count"] == 0
    assert coverage["coverage_ratio"] == 0.0
    assert coverage["degraded_search_mode"] is True


def test_coverage_states_uninitialized_partial_and_ready(tmp_path):
    db, repo, _ = _setup_db(tmp_path)
    ctx = _context("coverage_states")
    raw_hash = _seed_raw(db, "raw-phase13-twse_universe_master", "twse-universe-master", b"raw-coverage")

    # Allocate 2 instruments
    a1 = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330",
        first_observed_at="2026-09-01T00:00:00Z", source_reference="fix", context=ctx,
    )
    a2 = repo.allocate_instrument(
        venue="TWSE", official_code="2454", source_identity="twse:2454",
        first_observed_at="2026-09-01T00:00:00Z", source_reference="fix", context=ctx,
    )

    # Ingest Phase 19 style (parser_version="1", short_name=None) for both
    r1_2330 = repo.add_revision(
        context=ctx, idempotency_key="u-2330-v1", instrument_id=a1["instrument_id"],
        resource_id="twse-universe-master", logical_revision_key="twse:2330:master",
        revision_number=1, payload={
            "venue": "TWSE", "official_code": "2330", "canonical_symbol": "2330.TW",
            "display_name": "台灣積體電路製造股份有限公司", "security_type": "股票",
            "fetched_at": "2026-09-01T00:00:00Z", "received_at": "2026-09-01T00:00:00Z",
            "ingested_at": "2026-09-01T00:00:00Z", "available_at": "2026-09-01T00:00:00Z",
            "source_reference": "twse.t187ap03_L", "status": "accepted",
            "freshness_status": "current", "freshness_mode": "official_cadence_window",
            "current_complete": True, "coverage_complete": True, "parser_version": "1",
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash,
        },
    )
    r1_2454 = repo.add_revision(
        context=ctx, idempotency_key="u-2454-v1", instrument_id=a2["instrument_id"],
        resource_id="twse-universe-master", logical_revision_key="twse:2454:master",
        revision_number=1, payload={
            "venue": "TWSE", "official_code": "2454", "canonical_symbol": "2454.TW",
            "display_name": "聯發科技股份有限公司", "security_type": "股票",
            "fetched_at": "2026-09-01T00:00:00Z", "received_at": "2026-09-01T00:00:00Z",
            "ingested_at": "2026-09-01T00:00:00Z", "available_at": "2026-09-01T00:00:00Z",
            "source_reference": "twse.t187ap03_L", "status": "accepted",
            "freshness_status": "current", "freshness_mode": "official_cadence_window",
            "current_complete": True, "coverage_complete": True, "parser_version": "1",
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash,
        },
    )

    # State: short_names_uninitialized
    cov1 = repo.get_short_name_coverage()
    assert cov1["universe_status"] == "short_names_uninitialized"
    assert cov1["total_instruments"] == 2
    assert cov1["phase20_materialized_count"] == 0
    assert cov1["degraded_search_mode"] is True

    # Upgrade 2330 to v2 (parser_version="2.0.0", short_name="台積電")
    repo.add_revision(
        context=ctx, idempotency_key="u-2330-v2", instrument_id=a1["instrument_id"],
        resource_id="twse-universe-master", logical_revision_key="twse:2330:master",
        revision_number=2, payload={
            "venue": "TWSE", "official_code": "2330", "canonical_symbol": "2330.TW",
            "display_name": "台灣積體電路製造股份有限公司", "short_name": "台積電",
            "security_type": "股票", "fetched_at": "2026-09-05T00:00:00Z",
            "received_at": "2026-09-05T00:00:00Z", "ingested_at": "2026-09-05T00:00:00Z",
            "available_at": "2026-09-05T00:00:00Z", "source_reference": "twse.t187ap03_L",
            "status": "accepted", "freshness_status": "current",
            "freshness_mode": "official_cadence_window", "current_complete": True,
            "coverage_complete": True, "parser_version": "2.0.0",
            "supersedes_revision_id": r1_2330["universe_revision_id"],
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash,
        },
    )

    # State: short_names_partial (1 out of 2)
    cov2 = repo.get_short_name_coverage()
    assert cov2["universe_status"] == "short_names_partial"
    assert cov2["total_instruments"] == 2
    assert cov2["phase20_materialized_count"] == 1
    assert cov2["coverage_ratio"] == 0.5
    assert cov2["degraded_search_mode"] is True

    # Upgrade 2454 to v2 (parser_version="2.0.0", short_name="聯發科")
    repo.add_revision(
        context=ctx, idempotency_key="u-2454-v2", instrument_id=a2["instrument_id"],
        resource_id="twse-universe-master", logical_revision_key="twse:2454:master",
        revision_number=2, payload={
            "venue": "TWSE", "official_code": "2454", "canonical_symbol": "2454.TW",
            "display_name": "聯發科技股份有限公司", "short_name": "聯發科",
            "security_type": "股票", "fetched_at": "2026-09-05T00:00:00Z",
            "received_at": "2026-09-05T00:00:00Z", "ingested_at": "2026-09-05T00:00:00Z",
            "available_at": "2026-09-05T00:00:00Z", "source_reference": "twse.t187ap03_L",
            "status": "accepted", "freshness_status": "current",
            "freshness_mode": "official_cadence_window", "current_complete": True,
            "coverage_complete": True, "parser_version": "2.0.0",
            "supersedes_revision_id": r1_2454["universe_revision_id"],
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash,
        },
    )

    # State: ready (2 out of 2)
    cov3 = repo.get_short_name_coverage()
    assert cov3["universe_status"] == "ready"
    assert cov3["total_instruments"] == 2
    assert cov3["phase20_materialized_count"] == 2
    assert cov3["populated_short_names_count"] == 2
    assert cov3["coverage_ratio"] == 1.0
    assert cov3["degraded_search_mode"] is False


def test_coverage_excludes_delisted_instruments(tmp_path):
    db, repo, _ = _setup_db(tmp_path)
    ctx = _context("coverage_delisted")
    raw_hash = _seed_raw(db, "raw-phase13-twse_universe_master", "twse-universe-master", b"raw-delisted")

    a = repo.allocate_instrument(
        venue="TWSE", official_code="9999", source_identity="twse:9999",
        first_observed_at="2026-09-01T00:00:00Z", source_reference="fix", context=ctx,
    )
    repo.add_revision(
        context=ctx, idempotency_key="u-9999-v2", instrument_id=a["instrument_id"],
        resource_id="twse-universe-master", logical_revision_key="twse:9999:master",
        revision_number=1, payload={
            "venue": "TWSE", "official_code": "9999", "canonical_symbol": "9999.TW",
            "display_name": "已下市公司", "short_name": "已下市",
            "security_type": "股票", "fetched_at": "2026-09-01T00:00:00Z",
            "received_at": "2026-09-01T00:00:00Z", "ingested_at": "2026-09-01T00:00:00Z",
            "available_at": "2026-09-01T00:00:00Z", "source_reference": "twse.t187ap03_L",
            "status": "accepted", "freshness_status": "current",
            "freshness_mode": "official_cadence_window", "current_complete": True,
            "coverage_complete": True, "parser_version": "2.0.0",
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash,
        },
    )
    # Before delisting event: 1 instrument
    assert repo.get_short_name_coverage()["total_instruments"] == 1

    # Add lifecycle termination event
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO universe_lifecycle_events VALUES (
                'evt_delist_9999', ?, 'terminated', '2026-09-02', '2026-09-02T00:00:00Z',
                '2026-09-02T00:00:00Z', '2026-09-02T00:00:00Z', 'twse.termination', 'accepted', 'delisted'
            )
            """,
            (a["instrument_id"],),
        )

    # After delisting event: 0 instruments (delisted excluded)
    cov = repo.get_short_name_coverage(cutoff="2026-09-03T00:00:00Z")
    assert cov["total_instruments"] == 0
    assert cov["universe_status"] == "not_initialized"


def test_search_exact_code_prefix_and_short_name_ranking(tmp_path):
    db, repo, _ = _setup_db(tmp_path)
    ctx = _context("search_ranking")
    raw_hash = _seed_raw(db, "raw-phase13-twse_universe_master", "twse-universe-master", b"raw-search")

    # Ingest 3 stocks:
    # 2330 台積電
    # 2337 旺宏
    # 2454 聯發科
    stocks = [
        ("2330", "2330.TW", "台灣積體電路製造股份有限公司", "台積電"),
        ("2337", "2337.TW", "旺宏電子股份有限公司", "旺宏"),
        ("2454", "2454.TW", "聯發科技股份有限公司", "聯發科"),
    ]
    for code, symbol, display, short in stocks:
        anchor = repo.allocate_instrument(
            venue="TWSE", official_code=code, source_identity=f"twse:{code}",
            first_observed_at="2026-09-01T00:00:00Z", source_reference="fix", context=ctx,
        )
        repo.add_revision(
            context=ctx, idempotency_key=f"u-{code}-v2", instrument_id=anchor["instrument_id"],
            resource_id="twse-universe-master", logical_revision_key=f"twse:{code}:master",
            revision_number=1, payload={
                "venue": "TWSE", "official_code": code, "canonical_symbol": symbol,
                "display_name": display, "short_name": short, "security_type": "股票",
                "fetched_at": "2026-09-01T00:00:00Z", "received_at": "2026-09-01T00:00:00Z",
                "ingested_at": "2026-09-01T00:00:00Z", "available_at": "2026-09-01T00:00:00Z",
                "source_reference": "twse.t187ap03_L", "status": "accepted",
                "freshness_status": "current", "freshness_mode": "official_cadence_window",
                "current_complete": True, "coverage_complete": True, "parser_version": "2.0.0",
                "raw_resource_revision_id": "raw-phase13-twse_universe_master",
                "raw_payload_sha256": raw_hash,
            },
        )

    # 1. Exact code match: "2330" -> top match 2330.TW
    res_code = repo.search_instruments_local(query="2330")
    assert res_code["total_matches"] == 1
    assert res_code["results"][0]["canonical_symbol"] == "2330.TW"
    assert res_code["results"][0]["short_name"] == "台積電"

    # 2. Prefix match: "23" -> matches both 2330 and 2337, not 2454
    res_prefix = repo.search_instruments_local(query="23")
    assert res_prefix["total_matches"] == 2
    symbols = [r["canonical_symbol"] for r in res_prefix["results"]]
    assert "2330.TW" in symbols
    assert "2337.TW" in symbols
    assert "2454.TW" not in symbols

    # 3. Short name exact match: "台積電" -> 2330.TW
    res_short = repo.search_instruments_local(query="台積電")
    assert res_short["total_matches"] == 1
    assert res_short["results"][0]["canonical_symbol"] == "2330.TW"

    # 4. Short name substring: "聯發" -> 2454.TW
    res_sub = repo.search_instruments_local(query="聯發")
    assert res_sub["total_matches"] == 1
    assert res_sub["results"][0]["canonical_symbol"] == "2454.TW"

    # 5. Display name substring: "積體電路" -> 2330.TW
    res_display = repo.search_instruments_local(query="積體電路")
    assert res_display["total_matches"] == 1
    assert res_display["results"][0]["canonical_symbol"] == "2330.TW"


def test_api_v2_universe_search_and_coverage_endpoints(tmp_path, monkeypatch):
    db, repo, _ = _setup_db(tmp_path)
    ctx = _context("api_search")
    raw_hash = _seed_raw(db, "raw-phase13-twse_universe_master", "twse-universe-master", b"raw-api")

    anchor = repo.allocate_instrument(
        venue="TWSE", official_code="2330", source_identity="twse:2330",
        first_observed_at="2026-09-01T00:00:00Z", source_reference="fix", context=ctx,
    )
    repo.add_revision(
        context=ctx, idempotency_key="u-2330-api", instrument_id=anchor["instrument_id"],
        resource_id="twse-universe-master", logical_revision_key="twse:2330:master",
        revision_number=1, payload={
            "venue": "TWSE", "official_code": "2330", "canonical_symbol": "2330.TW",
            "display_name": "台灣積體電路製造股份有限公司", "short_name": "台積電",
            "security_type": "股票", "fetched_at": "2026-09-01T00:00:00Z",
            "received_at": "2026-09-01T00:00:00Z", "ingested_at": "2026-09-01T00:00:00Z",
            "available_at": "2026-09-01T00:00:00Z", "source_reference": "twse.t187ap03_L",
            "status": "accepted", "freshness_status": "current",
            "freshness_mode": "official_cadence_window", "current_complete": True,
            "coverage_complete": True, "parser_version": "2.0.0",
            "raw_resource_revision_id": "raw-phase13-twse_universe_master",
            "raw_payload_sha256": raw_hash,
        },
    )

    environ = {
        "TW_STOCK_PREDICTOR_ENV": "development",
        "DATABASE_PATH": str(db),
        "UNIVERSE_DB_PATH": str(db),
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
    }
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(db))
    monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", "http://127.0.0.1:8000")
    paths = RuntimePaths.from_environment(environ)
    settings = RuntimeSettings.from_environment(environ, paths=paths)
    settings.paths.ensure_user_dirs()
    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    # 1. Test coverage endpoint
    resp_cov = client.get("/api/v2/universe/coverage")
    assert resp_cov.status_code == 200
    data_cov = resp_cov.json()
    assert data_cov["universe_status"] == "ready"
    assert data_cov["total_instruments"] == 1
    assert data_cov["coverage_ratio"] == 1.0

    # 2. Test search endpoint
    resp_search = client.get("/api/v2/universe/search?q=台積電")
    assert resp_search.status_code == 200
    data_search = resp_search.json()
    assert data_search["total_matches"] == 1
    assert data_search["results"][0]["canonical_symbol"] == "2330.TW"
    assert data_search["results"][0]["short_name"] == "台積電"
    assert data_search["coverage"]["universe_status"] == "ready"
