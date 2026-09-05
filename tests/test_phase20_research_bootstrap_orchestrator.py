"""Phase 20 WP04: Tests for Research Bootstrap Orchestrator Service and API."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.domain.installed_data_operations import (
    InstalledOperationStatus,
    InstalledOperationType,
)
from src.repositories.current_research_repository import CurrentResearchRepository
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.runtime.settings import RuntimePaths, RuntimeSettings
from src.services.current_research_service import CurrentResearchService
from src.services.research_bootstrap_service import ResearchBootstrapService
from tests.phase13_test_support import seed_raw_provenance


def _setup_db(tmp_path):
    db = tmp_path / "phase20_bootstrap.sqlite"
    apply_valuation_migration(str(db))
    seed_raw_provenance(db)
    cur_repo = CurrentResearchRepository(str(db))
    cur_svc = CurrentResearchService(str(db), repository=cur_repo)
    ops_repo = InstalledDataOperationsRepository(str(db))
    return db, cur_svc, ops_repo


def _insert_snapshot_and_observation(
    conn: sqlite3.Connection,
    date: str = "2026-09-04",
    official_code: str = "2330",
    close: str = "980.0",
) -> None:
    raw_hash = hashlib.sha256(b"raw").hexdigest()
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_resource_revisions VALUES (
            'raw_b_1', 'id-1', 'twse-official', 'twse.eod.stock_day_all', 'fix',
            '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z',
            '2026-09-01T00:00:00Z', ?, '1', 'schema_fp', 'hash_only',
            NULL, 'fresh', 'eligible', NULL, 'fixture'
        )
        """,
        (raw_hash,),
    )
    snap_fp = hashlib.sha256(f"snap:{date}".encode()).hexdigest()
    conn.execute(
        """
        INSERT OR REPLACE INTO eod_close_source_snapshots (
            source_snapshot_id, resource_id, raw_resource_revision_id, logical_revision_key,
            revision_number, source_trade_date, source_trade_date_status, status,
            coverage_state, coverage_proof_type, coverage_proof_reference, row_count,
            source_date_min, source_date_max, source_published_at, fetched_at, received_at,
            available_at, ingested_at, source_url, http_method, response_format, contract_version,
            parser_version, schema_fingerprint, raw_payload_sha256, normalized_payload_sha256,
            query_dimensions_json, source_record_reference, source_scope, reason,
            supersedes_source_snapshot_id, revocation_reference, identity_fingerprint
        ) VALUES (
            'snap_b_1', 'twse.eod.stock_day_all', 'raw_b_1', 'key',
            1, ?, 'valid', 'available',
            'complete', NULL, NULL, 1,
            ?, ?, NULL, '2026-09-04T13:35:00Z', '2026-09-04T13:35:00Z',
            '2026-09-04T13:35:00Z', '2026-09-04T13:36:00Z', 'http://url', 'GET', 'json', 'v1',
            '1', 'fp', ?, ?,
            '{}', 'ref', 'scope', NULL,
            NULL, NULL, ?
        )
        """,
        (date, date, date, raw_hash, raw_hash, snap_fp),
    )
    obs_fp = hashlib.sha256(f"obs:{official_code}:{date}".encode()).hexdigest()
    conn.execute(
        """
        INSERT OR REPLACE INTO eod_close_observations (
            close_observation_id, resource_id, raw_resource_revision_id, source_snapshot_id,
            classification_evidence_id, instrument_id, instrument_revision_id,
            venue, official_code, trade_date, trade_date_status, revision_number,
            supersedes_observation_id, raw_close_text, close_value, raw_volume_text, volume_value,
            raw_trade_indication_text, trade_indication_value, currency, unit,
            price_semantics_version, product_scope, observation_status, public_eligibility_status,
            quality_status, quality_flags_json, row_fingerprint, raw_payload_sha256,
            normalized_payload_sha256, source_trading_scope, available_at, ingested_at,
            source_record_reference, source_note, identity_fingerprint
        ) VALUES (
            'obs_b_1', 'twse.eod.stock_day_all', 'raw_b_1', 'snap_b_1',
            NULL, NULL, NULL,
            'TWSE', ?, ?, 'valid', 1,
            NULL, ?, ?, '1000', '1000',
            '+', 'up', 'TWD', 'TWD_per_share',
            'phase14_v1', 'supported_stock', 'available', 'eligible',
            'fresh', '[]', 'rfp', ?,
            ?, 'regular', '2026-09-04T13:35:00Z', '2026-09-04T13:36:00Z',
            'ref', NULL, ?
        )
        """,
        (official_code, date, close, close, raw_hash, raw_hash, obs_fp),
    )


def test_bootstrap_returns_ready_when_eod_available(tmp_path):
    """When target symbol already has settled EOD observation, bootstrap returns status 'ready'."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_snapshot_and_observation(conn, date="2026-09-04", official_code="2330", close="980.0")

    bootstrap_svc = ResearchBootstrapService(
        db_path=str(db),
        current_research_service=cur_svc,
        operations_repo=ops_repo,
    )
    res = bootstrap_svc.bootstrap_symbol("2330.TW")
    assert res["status"] == "ready"
    assert res["canonical_symbol"] == "2330.TW"
    assert res["operation_id"] is None


def test_bootstrap_returns_preparing_when_active_operation_covers_target(tmp_path):
    """When an active operation already includes target symbol, bootstrap returns status 'preparing'."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
    op = ops_repo.create_operation(
        operation_id="op_active_2330",
        operation_type=InstalledOperationType.ENABLE_SYMBOL.value,
        lease_owner_id="owner-1",
        target_symbols=["2330.TW"],
    )

    bootstrap_svc = ResearchBootstrapService(
        db_path=str(db),
        current_research_service=cur_svc,
        operations_repo=ops_repo,
    )
    res = bootstrap_svc.bootstrap_symbol("2330.TW")
    assert res["status"] == "preparing"
    assert res["canonical_symbol"] == "2330.TW"
    assert res["operation_id"] == "op_active_2330"


def test_bootstrap_returns_waiting_when_active_operation_does_not_cover_target(tmp_path):
    """When an active operation does NOT cover target symbol, bootstrap returns 'waiting_for_data_operation'."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
    op = ops_repo.create_operation(
        operation_id="op_other_2454",
        operation_type=InstalledOperationType.ENABLE_SYMBOL.value,
        lease_owner_id="owner-1",
        target_symbols=["2454.TW"],
    )

    bootstrap_svc = ResearchBootstrapService(
        db_path=str(db),
        current_research_service=cur_svc,
        operations_repo=ops_repo,
    )
    res = bootstrap_svc.bootstrap_symbol("2330.TW")
    assert res["status"] == "waiting_for_data_operation"
    assert res["canonical_symbol"] == "2330.TW"
    assert res["operation_id"] == "op_other_2454"


def test_api_v2_research_bootstrap_endpoint(tmp_path, monkeypatch):
    """POST /api/v2/research/bootstrap endpoint returns 200 with status and operation_id."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_snapshot_and_observation(conn, date="2026-09-04", official_code="2330", close="980.0")

    environ = {
        "TW_STOCK_PREDICTOR_ENV": "development",
        "DATABASE_PATH": str(db),
        "UNIVERSE_DB_PATH": str(db),
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
    }
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", "http://127.0.0.1:8000")
    paths = RuntimePaths.from_environment(environ)
    settings = RuntimeSettings.from_environment(environ, paths=paths)
    settings.paths.ensure_user_dirs()
    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

    token_resp = client.get("/api/v2/data-operations/csrf-token")
    assert token_resp.status_code == 200
    token = token_resp.json()["csrf_token"]
    headers = {
        "Origin": "http://127.0.0.1:8000",
        "X-CSRF-Token": token,
        "Content-Type": "application/json",
    }

    resp = client.post("/api/v2/research/bootstrap", json={"canonical_symbol": "2330.TW"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["canonical_symbol"] == "2330.TW"
