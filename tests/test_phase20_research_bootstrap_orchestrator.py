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
    ops_repo.create_operation("recent", "enable_symbol", "test", target_symbols=["2330.TW"])
    ops_repo.finalize_operation("recent", "succeeded")
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
    ops_repo.create_operation("recent", "enable_symbol", "test", target_symbols=["2330.TW"])
    ops_repo.finalize_operation("recent", "succeeded")
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
    app.state.launch_handshake = {"launch_id": "test-launch-1"}
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


def test_bootstrap_missing_handshake_fails_closed_503(tmp_path, monkeypatch):
    """P1-3 regression: Missing or unvalidated launcher handshake must fail closed with 503 before operation creation."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
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
    # Explicitly verify app.state.launch_handshake is missing
    assert getattr(app.state, "launch_handshake", None) is None
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
    assert resp.status_code == 503
    assert resp.json()["detail"] == "launch_handshake_missing_or_unvalidated"
    # Ensure fail-closed: NO operation was created in repository
    assert ops_repo.get_active_operation() is None
    # Ensure no background workers registered
    assert len(getattr(app.state, "background_worker_threads", [])) == 0


def test_bootstrap_worker_registered_in_app_state_and_joined_on_shutdown(tmp_path, monkeypatch):
    """P1-4 regression: Background worker thread started by bootstrap is registered in app.state and joined on shutdown."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
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
    app.state.launch_handshake = {"launch_id": "test-launch-1"}

    # Mock InstalledDataSyncService.run_symbol_enablement_pipeline so worker completes cleanly
    monkeypatch.setattr(
        "src.services.installed_data_sync_service.InstalledDataSyncService.run_symbol_enablement_pipeline",
        lambda *args, **kwargs: None,
    )

    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
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
        assert data["status"] == "preparing"
        assert data["canonical_symbol"] == "2330.TW"
        assert data["operation_id"] is not None

        # Verify thread was registered in app.state.background_worker_threads
        workers = getattr(app.state, "background_worker_threads", [])
        assert len(workers) >= 1
        assert any("bootstrap-" in t.name for t in workers)

    # When TestClient context exits, FastAPI lifespan shutdown joins app.state.background_worker_threads
    for t in getattr(app.state, "background_worker_threads", []):
        assert not t.is_alive()


def test_bootstrap_returns_waiting_when_active_sync_operation_has_empty_targets(tmp_path):
    """P1-2 regression: Active SYNC with empty targets returns 'waiting_for_data_operation' when target EOD is not yet ready."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
    op = ops_repo.create_operation(
        operation_id="op_generic_sync",
        operation_type=InstalledOperationType.SYNC.value,
        lease_owner_id="owner-1",
        target_symbols=[],
    )

    bootstrap_svc = ResearchBootstrapService(
        db_path=str(db),
        current_research_service=cur_svc,
        operations_repo=ops_repo,
    )
    res = bootstrap_svc.bootstrap_symbol("2330.TW")
    assert res["status"] == "waiting_for_data_operation"
    assert res["canonical_symbol"] == "2330.TW"
    assert res["operation_id"] == "op_generic_sync"


def test_generic_sync_terminal_triggers_second_bootstrap_enable_symbol(tmp_path):
    """P1-A regression: Generic SYNC (empty targets) reaches terminal state, second bootstrap launches ENABLE_SYMBOL."""
    db, cur_svc, ops_repo = _setup_db(tmp_path)
    ops_repo.create_operation(
        operation_id="op_generic_sync",
        operation_type=InstalledOperationType.SYNC.value,
        lease_owner_id="owner-1",
        target_symbols=[],
    )

    bootstrap_svc = ResearchBootstrapService(
        db_path=str(db),
        current_research_service=cur_svc,
        operations_repo=ops_repo,
        runner_fn=lambda *args: None,
    )
    # 1. First bootstrap: waiting for unrelated SYNC
    res1 = bootstrap_svc.bootstrap_symbol("2330.TW")
    assert res1["status"] == "waiting_for_data_operation"
    assert res1["operation_id"] == "op_generic_sync"

    # 2. Unrelated SYNC reaches terminal state
    ops_repo.finalize_operation("op_generic_sync", status="succeeded")

    # 3. Second bootstrap: no active operation, launches ENABLE_SYMBOL for 2330.TW
    res2 = bootstrap_svc.bootstrap_symbol("2330.TW")
    assert res2["status"] == "preparing"
    assert res2["canonical_symbol"] == "2330.TW"
    assert res2["operation_id"] != "op_generic_sync"

    # Verify created operation
    active_op = ops_repo.get_operation_by_id(res2["operation_id"])
    assert active_op is not None
    assert active_op.operation_type == InstalledOperationType.ENABLE_SYMBOL.value
    assert json.loads(active_op.target_symbols_json) == ["2330.TW"]
    bootstrap_svc.join_workers(timeout=2.0)


def test_bootstrap_symbol_delegates_to_run_symbol_enablement_pipeline(tmp_path, monkeypatch):
    """P1-1 regression: Bootstrap truly delegates to run_symbol_enablement_pipeline and NOT generic stages."""
    import threading
    db, cur_svc, ops_repo = _setup_db(tmp_path)
    called_args = []
    generic_stages_called = []

    def mock_enablement(self, op_id, auth, symbol, deadline_monotonic=None, stop_event=None):
        called_args.append({
            "op_id": op_id,
            "auth": auth,
            "symbol": symbol,
            "deadline_monotonic": deadline_monotonic,
            "stop_event": stop_event,
        })

    def mock_generic_stage(*args, **kwargs):
        generic_stages_called.append(True)

    monkeypatch.setattr(
        "src.services.installed_data_sync_service.InstalledDataSyncService.run_symbol_enablement_pipeline",
        mock_enablement,
    )
    monkeypatch.setattr(
        "src.services.installed_data_sync_service.InstalledDataSyncService.run_stage_universe",
        mock_generic_stage,
    )
    monkeypatch.setattr(
        "src.services.installed_data_sync_service.InstalledDataSyncService.run_stage_turnover_and_cbc",
        mock_generic_stage,
    )

    bootstrap_svc = ResearchBootstrapService(
        db_path=str(db),
        current_research_service=cur_svc,
        operations_repo=ops_repo,
    )
    res = bootstrap_svc.bootstrap_symbol("2330.TW")
    assert res["status"] == "preparing"
    assert res["canonical_symbol"] == "2330.TW"

    bootstrap_svc.join_workers(timeout=5.0)

    # Assert run_symbol_enablement_pipeline was called exactly once with 2330.TW
    assert len(called_args) == 1
    assert called_args[0]["symbol"] == "2330.TW"
    assert called_args[0]["op_id"] == res["operation_id"]
    assert isinstance(called_args[0]["stop_event"], threading.Event)
    # Assert generic sync stages were never called
    assert len(generic_stages_called) == 0


def test_deterministic_worker_quiescence_on_app_shutdown(tmp_path, monkeypatch):
    """P1-2 regression: Deterministic worker shutdown, status persisted as interrupted, no lock survives."""
    import threading
    import time
    from src.domain.installed_data_operations import (
        BackgroundWorkerThread,
        InstalledOperationStatus,
        InstalledOperationType,
        InstalledWriteAuthorization,
        OperationCancelled,
    )

    db, cur_svc, ops_repo = _setup_db(tmp_path)
    environ = {
        "TW_STOCK_PREDICTOR_ENV": "development",
        "DATABASE_PATH": str(db),
        "UNIVERSE_DB_PATH": str(db),
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
    }
    paths = RuntimePaths.from_environment(environ)
    settings = RuntimeSettings.from_environment(environ, paths=paths)
    settings.paths.ensure_user_dirs()
    app = create_app(settings)
    app.state.launch_handshake = {"launch_id": "test-launch-1"}

    # Create an active operation in repository
    op_id = "op_shutdown_test"
    ops_repo.create_operation(
        operation_id=op_id,
        operation_type=InstalledOperationType.ENABLE_SYMBOL.value,
        lease_owner_id="owner-test",
        target_symbols=["2330.TW"],
    )

    stop_event = threading.Event()
    worker_stopped = threading.Event()
    worker_started = threading.Event()

    def _worker_loop():
        worker_started.set()
        while not stop_event.is_set():
            time.sleep(0.02)
        worker_stopped.set()

    auth = InstalledWriteAuthorization(
        operation_id=op_id,
        instance_id="test-instance",
        allowed_resource_ids=frozenset({"twse.isin.classification"}),
    )
    worker = BackgroundWorkerThread(
        target=_worker_loop,
        name=f"worker-{op_id}",
        operation_id=op_id,
        auth=auth,
        stop_event=stop_event,
        daemon=True,
    )

    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)):
        app.state.background_worker_threads.append(worker)
        worker.start()
        assert worker_started.wait(timeout=2.0)
        assert worker.is_alive()

    # Exiting TestClient triggers lifespan shutdown
    assert not worker.is_alive()
    assert worker_stopped.is_set()
    assert auth.revoked is True

    # Check that the operation in the DB was transitioned to INTERRUPTED
    active = ops_repo.get_operation_by_id(op_id)
    assert active is not None
    assert active.status == InstalledOperationStatus.INTERRUPTED.value
    assert "interrupted by application shutdown" in (active.error_detail or "").lower()

    # Windows lock check: database can be opened and written immediately without permission error
    with sqlite3.connect(db) as conn:
        conn.execute("VACUUM")


def test_deterministic_worker_quiescence_raises_when_worker_fails_to_stop(tmp_path, monkeypatch):
    """P1-2 regression: If a rogue worker ignores stop request and does not quiesce, lifespan raises RuntimeError."""
    import threading
    import time
    from src.domain.installed_data_operations import BackgroundWorkerThread

    db, cur_svc, ops_repo = _setup_db(tmp_path)
    environ = {
        "TW_STOCK_PREDICTOR_ENV": "development",
        "DATABASE_PATH": str(db),
        "UNIVERSE_DB_PATH": str(db),
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
        "WORKER_SHUTDOWN_TIMEOUT_SECONDS": "0.1",
    }
    monkeypatch.setenv("WORKER_SHUTDOWN_TIMEOUT_SECONDS", "0.1")
    paths = RuntimePaths.from_environment(environ)
    settings = RuntimeSettings.from_environment(environ, paths=paths)
    settings.paths.ensure_user_dirs()
    app = create_app(settings)
    app.state.launch_handshake = {"launch_id": "test-launch-1"}

    stop_event = threading.Event()
    worker_started = threading.Event()

    # Rogue thread that deliberately ignores stop_event and sleeps
    def _rogue_loop():
        worker_started.set()
        time.sleep(1.0)

    worker = BackgroundWorkerThread(
        target=_rogue_loop,
        name="rogue-worker",
        operation_id="op_rogue",
        stop_event=stop_event,
        daemon=True,
    )

    with pytest.raises(RuntimeError, match="Deterministic shutdown failed"):
        with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)):
            app.state.background_worker_threads.append(worker)
            worker.start()
            assert worker_started.wait(timeout=2.0)
