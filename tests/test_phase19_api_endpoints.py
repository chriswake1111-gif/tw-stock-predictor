"""Tests for Phase 19 Data Operations API endpoints under /api/v2/data-operations."""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.domain.installed_data_operations import (
    InstalledOperationStatus,
    InstalledOperationType,
)
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.runtime.settings import RuntimePaths, RuntimeSettings


@pytest.fixture
def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, InstalledDataOperationsRepository, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        app_dir = Path(temp_dir)
        db_file = app_dir / "data" / "test.db"
        environ = {
            "TW_STOCK_PACKAGED": "0",
            "TW_STOCK_DATA_ROOT": str(app_dir / "data"),
            "DATABASE_PATH": str(db_file),
            "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
        }
        monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", "http://127.0.0.1:8000")
        monkeypatch.delenv("RESEARCH_ALLOWED_DEV_ORIGINS", raising=False)
        paths = RuntimePaths.from_environment(environ)
        settings = RuntimeSettings.from_environment(environ, paths=paths)
        settings.paths.ensure_user_dirs()
        apply_valuation_migration(str(db_file))

        app = create_app(settings=settings)
        app.state.launch_handshake = {"launch_id": "test-launch-1"}
        client = TestClient(
            app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
        )
        repo = InstalledDataOperationsRepository(str(db_file))
        try:
            yield client, repo, str(db_file)
        finally:
            del client
            del repo
            del app
            gc.collect()


def _csrf(client: TestClient) -> tuple[str, dict[str, str]]:
    res = client.get("/api/v2/data-operations/csrf-token")
    assert res.status_code == 200
    token = res.json()["csrf_token"]
    headers = {
        "Origin": "http://127.0.0.1:8000",
        "X-CSRF-Token": token,
        "Content-Type": "application/json",
    }
    return token, headers


def test_csrf_token_endpoint(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, _, _ = api_client
    response = client.get("/api/v2/data-operations/csrf-token")
    assert response.status_code == 200
    data = response.json()
    assert "csrf_token" in data
    assert "expires_at" in data
    assert "research_csrf_session" in response.cookies


def test_get_status_is_public_and_read_only(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, _, _ = api_client
    response = client.get("/api/v2/data-operations/status")
    assert response.status_code == 200
    data = response.json()
    assert "readiness" in data
    assert "is_syncing" in data
    assert "market_context_summary" in data
    assert data["is_syncing"] is False


def test_get_operation_by_id_404_and_200(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, repo, _ = api_client

    # 404 for non-existent operation
    res_404 = client.get("/api/v2/data-operations/operations/op_nonexistent")
    assert res_404.status_code == 404

    # 200 for created operation
    op = repo.create_operation("op_exists_123", InstalledOperationType.SYNC.value, "launch-1")
    res_200 = client.get("/api/v2/data-operations/operations/op_exists_123")
    assert res_200.status_code == 200
    data = res_200.json()
    assert data["operation_id"] == "op_exists_123"
    assert data["status"] == "running"
    assert "items" in data


def test_cancel_active_operation(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, repo, _ = api_client
    _, headers = _csrf(client)

    # 404 when no active operation exists
    res_no_active = client.post("/api/v2/data-operations/cancel", headers=headers, json={})
    assert res_no_active.status_code == 404

    # Create a running operation
    op = repo.create_operation("op_active_cancel", InstalledOperationType.SYNC.value, "launch-1")
    res_cancel = client.post("/api/v2/data-operations/cancel", headers=headers, json={})
    assert res_cancel.status_code == 200
    cancel_data = res_cancel.json()
    assert cancel_data["operation_id"] == "op_active_cancel"
    assert cancel_data["status"] == "cancelling"


def test_enable_symbol_endpoint(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, repo, _ = api_client
    _, headers = _csrf(client)
    res_enable = client.post(
        "/api/v2/data-operations/symbols/2330.TW/enable", headers=headers, json={}
    )
    assert res_enable.status_code == 200
    data = res_enable.json()
    assert "operation_id" in data
    assert data["status"] == "running"
    assert data["current_stage"] == "classification"


def test_missing_handshake_fails_closed(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, _, _ = api_client
    client.app.state.launch_handshake = None
    _, headers = _csrf(client)
    res = client.post(
        "/api/v2/data-operations/symbols/2330.TW/enable", headers=headers, json={}
    )
    assert res.status_code == 503
    assert "launch_handshake_missing" in res.json()["detail"]


def test_deadline_exceeding_90s_rejected(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, _, _ = api_client
    _, headers = _csrf(client)
    res = client.post(
        "/api/v2/data-operations/sync",
        headers=headers,
        json={"deadline_seconds": 120.0},
    )
    assert res.status_code == 422


def test_unapproved_write_aliases_return_404(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, _, _ = api_client
    _, headers = _csrf(client)
    res_enable_alias = client.post(
        "/api/v2/data-operations/enable-symbol",
        headers=headers,
        json={"symbol": "2330.TW"},
    )
    assert res_enable_alias.status_code in (404, 405)

    res_cancel_alias = client.post(
        "/api/v2/data-operations/operations/op_123/cancel",
        headers=headers,
        json={},
    )
    assert res_cancel_alias.status_code in (404, 405)
