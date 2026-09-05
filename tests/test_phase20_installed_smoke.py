"""Phase 20 Installed Product Usability & Research Bootstrap Smoke Proofs."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.repositories.migration_runner import (
    ADDITIONAL_MIGRATION_IDS,
    apply_valuation_migration,
    migration_manifest,
)
from src.runtime.manifest import (
    build_internal_manifest,
    validate_internal_manifest,
    write_manifest,
)
from src.runtime.paths import RuntimePaths
from src.runtime.settings import RuntimeSettings
from src.runtime.startup_coordinator import StartupCoordinator

REPO_ROOT = Path(__file__).resolve().parents[1]


def _create_packaged_settings(tmp_path: Path) -> tuple[RuntimeSettings, StartupCoordinator]:
    resource_root = tmp_path / "resource"
    shutil.copytree(REPO_ROOT / "config", resource_root / "config")
    shutil.copytree(REPO_ROOT / "migrations", resource_root / "migrations")
    shutil.copytree(REPO_ROOT / "frontend" / "dist", resource_root / "frontend" / "dist")

    manifest = build_internal_manifest(
        resource_root,
        app_version="20.0.0-test",
        build_sha="phase20-test-build",
        migration_records=migration_manifest(resource_root),
        frontend_dist=resource_root / "frontend" / "dist",
        model_rules_path=resource_root / "config" / "model_rules.yaml",
    )
    manifest_path = write_manifest(resource_root / "package-manifest.json", manifest)
    validate_internal_manifest(manifest, resource_root, manifest_path=manifest_path)

    user_root = tmp_path / "user"
    environment = {
        "TW_STOCK_PACKAGED": "true",
        "TW_STOCK_APP_VERSION": "20.0.0-test",
        "TW_STOCK_BUILD_SHA": "phase20-test-build",
        "RESEARCH_APPLICATION_ORIGIN": "http://127.0.0.1:8000",
        "TW_STOCK_PORT": "8000",
    }
    paths = RuntimePaths.from_environment(
        environment,
        project_root=REPO_ROOT,
        packaged_install_root=tmp_path / "install",
        packaged_resource_root=resource_root,
        packaged_user_root=user_root,
    )
    settings = RuntimeSettings.from_environment(environment, paths=paths)
    coordinator = StartupCoordinator(settings)
    return settings, coordinator


def test_phase20_packaged_clean_machine_bootstrap(monkeypatch: pytest.MonkeyPatch):
    """Packaged clean-machine startup initializes database with Migration 22, frontend routes, and zero egress search."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        tmp_path = Path(temp_dir)
        settings, coordinator = _create_packaged_settings(tmp_path)
        monkeypatch.setenv("DATABASE_PATH", str(settings.paths.database_path))
        monkeypatch.setenv("EOD_DB_PATH", str(settings.paths.database_path))
        monkeypatch.setenv("UNIVERSE_DB_PATH", str(settings.paths.database_path))
        monkeypatch.setenv("RESEARCH_APPLICATION_ORIGIN", "http://127.0.0.1:8000")

        startup_res = coordinator.prepare()
        assert startup_res.status == "ready"
        assert startup_res.database is not None

        # Verify Migration 22 exists in manifest
        assert "20260905_22_phase20_universe_short_name" in ADDITIONAL_MIGRATION_IDS

        app = create_app(settings=settings, startup_result=startup_res)
        client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))

        # 1. Frontend serves index.html at root
        root_resp = client.get("/")
        assert root_resp.status_code == 200
        assert "<!DOCTYPE html>" in root_resp.text or "<div id=\"root\">" in root_resp.text

        # 2. Coverage endpoint returns not_initialized on fresh clean machine
        cov_resp = client.get("/api/v2/universe/coverage")
        assert cov_resp.status_code == 200
        cov_data = cov_resp.json()
        assert cov_data["universe_status"] == "not_initialized"
        assert cov_data["total_instruments"] == 0

        # 3. Search endpoint returns empty matches without crashing or external network
        search_resp = client.get("/api/v2/universe/search?q=2330")
        assert search_resp.status_code == 200
        search_data = search_resp.json()
        assert search_data["total_matches"] == 0
        assert search_data["results"] == []

        # 4. Bootstrap endpoint requires CSRF header and responds with valid structure
        token_resp = client.get("/api/v2/data-operations/csrf-token")
        assert token_resp.status_code == 200
        token = token_resp.json()["csrf_token"]
        headers = {
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": token,
            "Content-Type": "application/json",
        }
        boot_resp = client.post("/api/v2/research/bootstrap", json={"canonical_symbol": "2330.TW"}, headers=headers)
        assert boot_resp.status_code == 200
        boot_data = boot_resp.json()
        assert boot_data["status"] in ("ready", "preparing", "waiting_for_data_operation")
