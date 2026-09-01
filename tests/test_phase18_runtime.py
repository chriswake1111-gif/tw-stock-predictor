"""Phase 18 local Windows productization contracts."""

from __future__ import annotations

import json
import importlib.util
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from src.repositories.migration_runner import apply_valuation_migration, migration_manifest
from src.repositories.research_workflow_repository import ResearchWorkflowRepository
from src.runtime.database_state import DatabaseState, classify_database
from src.runtime.control import LocalStopEvent, stop_event_name
from src.runtime.diagnostics import DiagnosticLogger
from src.runtime.instance import LaunchContext, LaunchHandshakeError, validate_launch_context
from src.runtime.manifest import (
    ManifestError,
    build_internal_manifest,
    validate_internal_manifest,
    write_manifest,
)
from src.runtime.paths import RuntimePaths
from src.runtime.paths import RuntimePathError
from src.runtime.restore import restore_backup_and_activate
from src.runtime.settings import RuntimeSettings, packaged_auto_migrate
from src.runtime.startup_coordinator import StartupCoordinator
from src.services.evidence_backup_service import EvidenceBackupService


REPO_ROOT = Path(__file__).resolve().parents[1]


def _packaged_settings(tmp_path: Path) -> tuple[RuntimeSettings, Path]:
    resource_root = tmp_path / "resource"
    shutil.copytree(REPO_ROOT / "config", resource_root / "config")
    shutil.copytree(REPO_ROOT / "migrations", resource_root / "migrations")
    shutil.copytree(REPO_ROOT / "frontend" / "dist", resource_root / "frontend" / "dist")
    manifest = build_internal_manifest(
        resource_root,
        app_version="18.0.0-test",
        build_sha="phase18-test-build",
        migration_records=migration_manifest(resource_root),
        frontend_dist=resource_root / "frontend" / "dist",
        model_rules_path=resource_root / "config" / "model_rules.yaml",
    )
    manifest_path = write_manifest(resource_root / "package-manifest.json", manifest)
    validate_internal_manifest(manifest, resource_root, manifest_path=manifest_path)
    user_root = tmp_path / "user"
    environment = {
        "TW_STOCK_PACKAGED": "true",
        "TW_STOCK_RESOURCE_ROOT": str(resource_root),
        "TW_STOCK_USER_ROOT": str(user_root),
        "TW_STOCK_INSTALL_ROOT": str(tmp_path / "install"),
        "TW_STOCK_APP_VERSION": "",
        "TW_STOCK_BUILD_SHA": "",
    }
    paths = RuntimePaths.from_environment(environment, project_root=REPO_ROOT)
    settings = RuntimeSettings.from_environment(environment, paths=paths)
    return settings, resource_root


def test_phase18_manifest_is_relative_and_rejects_path_escape(tmp_path):
    settings, resource_root = _packaged_settings(tmp_path)
    manifest_path = resource_root / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert all(not Path(item["path"]).is_absolute() for item in manifest["resources"])
    assert all(not Path(item["path"]).is_absolute() for item in manifest["migrations"])

    tampered = dict(manifest)
    tampered["resources"] = [dict(manifest["resources"][0], path="../outside.txt")]
    with pytest.raises(ManifestError, match="path is invalid|escapes root"):
        validate_internal_manifest(tampered, resource_root, manifest_path=manifest_path)
    assert settings.build_sha == "phase18-test-build"


def test_phase18_database_states_are_read_only_and_explicit(tmp_path):
    settings, resource_root = _packaged_settings(tmp_path)
    missing = settings.paths.database_path
    assert classify_database(missing, resource_root=resource_root).state is DatabaseState.FRESH
    assert not missing.exists()

    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE legacy_rows(value TEXT)")
    assert classify_database(legacy, resource_root=resource_root).state is DatabaseState.LEGACY

    current = tmp_path / "current.db"
    apply_valuation_migration(str(current), resource_root=resource_root)
    assert classify_database(current, resource_root=resource_root).state is DatabaseState.KNOWN_V2_CURRENT
    with sqlite3.connect(current) as conn:
        conn.execute(
            "DELETE FROM additive_schema_migrations WHERE version_id=?",
            ("20260828_20_phase14_third_code_review_remediation",),
        )
    upgradeable = classify_database(current, resource_root=resource_root)
    assert upgradeable.state is DatabaseState.KNOWN_V2_UPGRADEABLE
    assert "20260828_20_phase14_third_code_review_remediation" in upgradeable.pending_migration_ids


def test_phase18_startup_migrates_fresh_and_seeds_user_config(tmp_path):
    settings, resource_root = _packaged_settings(tmp_path)
    result = StartupCoordinator(settings).prepare()
    assert result.ready
    assert result.database is not None
    assert result.database.state is DatabaseState.KNOWN_V2_CURRENT
    assert settings.paths.config_path.is_file()
    assert settings.paths.database_path.is_file()
    assert (resource_root / "config" / "config.yaml").is_file()


def test_phase18_upgrade_creates_pre_upgrade_evidence(tmp_path):
    settings, resource_root = _packaged_settings(tmp_path)
    settings.paths.ensure_user_dirs()
    apply_valuation_migration(str(settings.paths.database_path), resource_root=resource_root)
    with sqlite3.connect(settings.paths.database_path) as conn:
        conn.execute(
            "DELETE FROM additive_schema_migrations WHERE version_id=?",
            ("20260828_20_phase14_third_code_review_remediation",),
        )
    result = StartupCoordinator(settings).prepare()
    assert result.ready
    assert result.pre_upgrade_backup is not None
    assert result.pre_upgrade_backup.is_file()
    backup_validation = EvidenceBackupService.validate(str(result.pre_upgrade_backup))
    assert backup_validation["backup_metadata"]["reason"] == "pre_upgrade"


def test_phase18_legacy_database_is_preserved_before_v2_activation(tmp_path):
    settings, resource_root = _packaged_settings(tmp_path)
    settings.paths.ensure_user_dirs()
    conn = sqlite3.connect(settings.paths.database_path)
    try:
        conn.execute("CREATE TABLE legacy_rows(value TEXT)")
        conn.execute("INSERT INTO legacy_rows VALUES ('preserve-me')")
        conn.commit()
    finally:
        conn.close()
    result = StartupCoordinator(settings).prepare()
    assert result.ready
    assert result.legacy_archive is not None
    assert result.legacy_archive.is_file()
    with sqlite3.connect(result.legacy_archive) as conn:
        assert conn.execute("SELECT value FROM legacy_rows").fetchone() == ("preserve-me",)
    assert classify_database(
        settings.paths.database_path,
        resource_root=resource_root,
    ).state is DatabaseState.KNOWN_V2_CURRENT


def test_phase18_corrupt_database_fails_closed(tmp_path):
    settings, _ = _packaged_settings(tmp_path)
    settings.paths.ensure_user_dirs()
    settings.paths.database_path.write_bytes(b"not a sqlite database")
    result = StartupCoordinator(settings).prepare()
    assert result.status == "failed"
    assert result.failure_code == "database_corrupt_unknown"


def test_phase18_packaged_readiness_is_safe_and_scheduler_disabled(tmp_path):
    settings, resource_root = _packaged_settings(tmp_path)
    startup = StartupCoordinator(settings).prepare()
    endpoint = settings.with_endpoint(port=43127)
    app = create_app(endpoint, startup_result=startup)
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "tw_stock_ready_v1"
    assert payload["ready"] is True
    assert payload["origin"] == "http://127.0.0.1:43127"
    assert payload["scheduler_enabled"] is False
    assert resource_root.exists()


def test_phase18_packaged_request_helpers_do_not_auto_migrate(tmp_path, monkeypatch):
    settings, _ = _packaged_settings(tmp_path)
    missing = settings.paths.database_path
    monkeypatch.setenv("TW_STOCK_PACKAGED", "true")
    monkeypatch.setenv("DATABASE_PATH", str(missing))
    assert packaged_auto_migrate() is False
    from src.api.routes.v2_valuation import _performance_validation_service, _service

    _service()
    _performance_validation_service()
    assert not missing.exists()


def test_phase18_recovery_activates_local_candidate_and_preserves_backup(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    canonical = tmp_path / "canonical.db"
    ResearchWorkflowRepository(str(source)).add_membership("2330.TW")
    ResearchWorkflowRepository(str(canonical)).add_membership("2317.TW")
    EvidenceBackupService.backup(str(source), str(backup))
    result = restore_backup_and_activate(
        backup,
        canonical,
        backup_root=tmp_path / "backups",
        runtime_dir=tmp_path / "runtime",
    )
    assert result["status"] == "activated"
    assert result["reopened_and_revalidated"] is True
    assert backup.is_file()
    assert result["prior_canonical_path"] is not None
    with sqlite3.connect(canonical) as conn:
        assert conn.execute("SELECT symbol FROM research_watchlist_items").fetchall() == [("2330.TW",)]


def test_phase18_diagnostics_redact_nonce_and_bound_log_segments(tmp_path):
    logger = DiagnosticLogger(
        tmp_path,
        "runtime.log",
        max_bytes=220,
        backup_count=2,
        max_logical_bytes=440,
    )
    logger.emit(
        "test_event",
        phase="test",
        nonce="do-not-log",
        message="x" * 100,
    )
    for index in range(12):
        logger.emit("repeat", phase="test", index=index, message="y" * 100)
    contents = "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("runtime.log*"))
    assert "do-not-log" not in contents
    assert len(list(tmp_path.glob("runtime.log.*"))) <= 2
    assert sum(path.stat().st_size for path in tmp_path.glob("runtime.log*")) <= 440


def test_phase18_launch_handshake_requires_nonce_build_and_parent(tmp_path):
    context = LaunchContext.create(
        tmp_path,
        app_version="18.0.0",
        build_sha="build-a",
        launcher_pid=1234,
    )
    validated = validate_launch_context(
        context.context_path,
        launch_id=context.launch_id,
        nonce=context.nonce,
        launcher_pid=1234,
        expected_build_sha="build-a",
        current_pid=5678,
        parent_pid_resolver=lambda pid: 1234,
    )
    assert validated["launch_id"] == context.launch_id
    with pytest.raises(LaunchHandshakeError, match="nonce"):
        validate_launch_context(
            context.context_path,
            launch_id=context.launch_id,
            nonce="wrong",
            launcher_pid=1234,
            expected_build_sha="build-a",
            current_pid=5678,
            parent_pid_resolver=lambda pid: 1234,
        )


def test_phase18_package_validator_accepts_clean_resource_payload(tmp_path):
    settings, resource_root = _packaged_settings(tmp_path)
    package_root = tmp_path / "package"
    package_root.mkdir()
    shutil.copytree(resource_root, package_root / "resource-payload")
    executables = package_root / "executables"
    executables.mkdir()
    launcher_bundle = executables / "tw-stock-predictor"
    server_bundle = executables / "tw-stock-predictor-server"
    launcher_bundle.mkdir()
    server_bundle.mkdir()
    (launcher_bundle / "tw-stock-predictor.exe").write_bytes(b"launcher")
    (launcher_bundle / "_internal").mkdir()
    (launcher_bundle / "_internal" / "python312.dll").write_bytes(b"runtime")
    dependency_data = launcher_bundle / "_internal" / "dependency" / "data"
    dependency_data.mkdir(parents=True)
    (dependency_data / "module.dat").write_bytes(b"bundled dependency data")
    (server_bundle / "tw-stock-predictor-server.exe").write_bytes(b"server")
    (server_bundle / "_internal").mkdir()
    (server_bundle / "_internal" / "python312.dll").write_bytes(b"runtime")
    from tools.validate_windows_package import validate_package

    result = validate_package(package_root)
    assert result["status"] == "valid"
    assert result["executables"]["tw-stock-predictor.exe"] is True
    assert settings.paths.resource_root == resource_root


def test_phase18_installer_exposes_local_stop_shortcut():
    installer_script = (REPO_ROOT / "packaging" / "windows" / "tw-stock-predictor.iss").read_text(
        encoding="utf-8"
    )
    assert 'Name: "{autoprograms}\\Stop TW Stock Predictor"' in installer_script
    assert 'Parameters: "--stop"' in installer_script


def test_phase18_packaged_mutable_paths_cannot_overlap_install_resources(tmp_path):
    resource_root = tmp_path / "resource"
    install_root = tmp_path / "install"
    user_root = tmp_path / "user"
    environment = {
        "TW_STOCK_PACKAGED": "true",
        "TW_STOCK_RESOURCE_ROOT": str(resource_root),
        "TW_STOCK_INSTALL_ROOT": str(install_root),
        "TW_STOCK_USER_ROOT": str(install_root / "mutable"),
    }
    with pytest.raises(RuntimePathError, match="overlaps install_root"):
        RuntimePaths.from_environment(environment, project_root=REPO_ROOT)

    safe_environment = dict(environment, TW_STOCK_USER_ROOT=str(user_root), DATABASE_PATH=str(resource_root / "cache.db"))
    with pytest.raises(RuntimePathError, match="overlaps resource_root"):
        RuntimePaths.from_environment(safe_environment, project_root=REPO_ROOT)


def test_phase18_local_stop_event_is_graceful_control_plane(tmp_path):
    name = stop_event_name("phase18-test")
    event = LocalStopEvent.create(name)
    try:
        assert event.wait(0.0) is False
        event.set()
        with LocalStopEvent.open(name) as opened:
            assert opened.wait(0.1) is True
    finally:
        event.close()


@pytest.mark.skipif(os.name != "nt", reason="requires native Win32 APIs")
def test_phase18_win32_interop_declares_pointer_sized_handle_signatures():
    import ctypes
    from ctypes import wintypes

    from src.runtime import win32

    assert win32._kernel32.CreateMutexW.restype is wintypes.HANDLE
    assert win32._kernel32.OpenProcess.restype is wintypes.HANDLE
    assert win32._kernel32.CloseHandle.argtypes == [wintypes.HANDLE]
    assert ctypes.sizeof(wintypes.HANDLE) == ctypes.sizeof(ctypes.c_void_p)


def test_phase18_frozen_entrypoints_force_packaged_mode(monkeypatch):
    def load_entrypoint(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    launcher_entry = load_entrypoint(
        "phase18_launcher_entry",
        REPO_ROOT / "packaging" / "windows" / "launcher_entry.py",
    )
    server_entry = load_entrypoint(
        "phase18_server_entry",
        REPO_ROOT / "packaging" / "windows" / "server_entry.py",
    )

    monkeypatch.delenv("TW_STOCK_PACKAGED", raising=False)
    assert launcher_entry._packaged_settings().packaged is True
    assert server_entry._packaged_settings().packaged is True
