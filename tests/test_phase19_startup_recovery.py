"""Tests for Phase 19 crash/startup recovery lifecycle."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.domain.installed_data_operations import (
    InstalledOperationStatus,
    InstalledOperationType,
)
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.repositories.migration_runner import apply_valuation_migration
from src.runtime.settings import RuntimePaths, RuntimeSettings
from src.runtime.startup_coordinator import StartupCoordinator


def test_recover_interrupted_operations_reconciles_active_only() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        db_path = str(Path(temp_dir) / "test.db")
        apply_valuation_migration(db_path)
        repo = InstalledDataOperationsRepository(db_path)

        # 1. Running operation is recovered to interrupted
        repo.create_operation("op-run", InstalledOperationType.SYNC.value, "launch-1")
        assert repo.recover_interrupted_operations() == 1
        assert repo.get_operation_by_id("op-run").status == InstalledOperationStatus.INTERRUPTED.value

        # 2. Cancelling operation is recovered to interrupted
        repo.create_operation("op-cancel", InstalledOperationType.SYNC.value, "launch-1")
        assert repo.request_cancel("op-cancel") is True
        assert repo.recover_interrupted_operations() == 1
        assert repo.get_operation_by_id("op-cancel").status == InstalledOperationStatus.INTERRUPTED.value

        # 3. Terminal operations (succeeded) are untouched
        repo.create_operation("op-succ", InstalledOperationType.SYNC.value, "launch-1")
        repo.finalize_operation("op-succ", InstalledOperationStatus.SUCCEEDED.value)
        assert repo.recover_interrupted_operations() == 0
        assert repo.get_operation_by_id("op-succ").status == InstalledOperationStatus.SUCCEEDED.value


def test_startup_coordinator_prepare_runs_recovery() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        app_dir = Path(temp_dir)
        db_file = app_dir / "data" / "isolated.db"
        environ = {
            "TW_STOCK_PACKAGED": "0",
            "TW_STOCK_DATA_ROOT": str(app_dir / "data"),
            "DATABASE_PATH": str(db_file),
        }
        paths = RuntimePaths.from_environment(environ)
        settings = RuntimeSettings.from_environment(
            environ,
            paths=paths,
        )
        settings.paths.ensure_user_dirs()
        apply_valuation_migration(str(db_file))
        repo = InstalledDataOperationsRepository(str(db_file))

        # Leave a stale running operation from a simulated crash
        repo.create_operation("op-crashed-unique", InstalledOperationType.SYNC.value, "launch-crashed")

        coordinator = StartupCoordinator(settings)
        result = coordinator.prepare()

        assert result.status == "ready"
        op = repo.get_operation_by_id("op-crashed-unique")
        assert op is not None
        assert op.status == InstalledOperationStatus.INTERRUPTED.value
        assert op.error_detail == "interrupted_by_server_restart"
