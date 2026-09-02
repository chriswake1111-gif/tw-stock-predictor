"""Tests for Phase 19 Installed Data API endpoints."""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.collectors.installed_egress_client import InstalledEgressClient
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
def api_client() -> tuple[TestClient, InstalledDataOperationsRepository, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        app_dir = Path(temp_dir)
        db_file = app_dir / "data" / "test.db"
        environ = {
            "TW_STOCK_PACKAGED": "0",
            "TW_STOCK_DATA_ROOT": str(app_dir / "data"),
            "DATABASE_PATH": str(db_file),
        }
        paths = RuntimePaths.from_environment(environ)
        settings = RuntimeSettings.from_environment(environ, paths=paths)
        settings.paths.ensure_user_dirs()
        apply_valuation_migration(str(db_file))

        app = create_app(settings=settings)
        client = TestClient(app)
        repo = InstalledDataOperationsRepository(str(db_file))
        try:
            yield client, repo, str(db_file)
        finally:
            del client
            del repo
            del app
            gc.collect()


def test_get_status_is_public_and_read_only(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, _, _ = api_client
    response = client.get("/api/v2/installed-data/status")
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
    res_404 = client.get("/api/v2/installed-data/operations/op_nonexistent")
    assert res_404.status_code == 404

    # 200 for created operation
    op = repo.create_operation("op_exists_123", InstalledOperationType.SYNC.value, "launch-1")
    res_200 = client.get("/api/v2/installed-data/operations/op_exists_123")
    assert res_200.status_code == 200
    data = res_200.json()
    assert data["operation_id"] == "op_exists_123"
    assert data["status"] == "running"
    assert "items" in data


def test_post_sync_and_cancel_endpoints(
    api_client: tuple[TestClient, InstalledDataOperationsRepository, str]
) -> None:
    client, repo, _ = api_client

    # Create a running operation to cancel
    op = repo.create_operation("op_to_cancel", InstalledOperationType.SYNC.value, "launch-1")
    res_cancel = client.post(f"/api/v2/installed-data/operations/{op.operation_id}/cancel")
    assert res_cancel.status_code == 200
    cancel_data = res_cancel.json()
    assert cancel_data["operation_id"] == "op_to_cancel"
    assert cancel_data["status"] == "cancelling"

    # Finalize to succeeded, subsequent cancel should fail with 400
    repo.finalize_operation("op_to_cancel", status=InstalledOperationStatus.SUCCEEDED.value)
    res_cancel_again = client.post(f"/api/v2/installed-data/operations/{op.operation_id}/cancel")
    assert res_cancel_again.status_code == 400
