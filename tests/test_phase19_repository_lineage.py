"""Tests for Phase 19 InstalledDataOperationsRepository and item lineage."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.domain.installed_data_operations import (
    InstalledItemStatus,
    InstalledOperationStage,
    InstalledOperationStatus,
    InstalledOperationType,
)
from src.repositories.installed_data_operations_repository import (
    InstalledDataOperationsRepository,
)
from src.repositories.migration_runner import apply_valuation_migration


@pytest.fixture
def repo() -> tuple[InstalledDataOperationsRepository, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "test.db")
        apply_valuation_migration(db_path)
        yield InstalledDataOperationsRepository(db_path), db_path


def test_operation_lifecycle_crud(repo: tuple[InstalledDataOperationsRepository, str]) -> None:
    repository, _ = repo

    # 1. Create operation
    op = repository.create_operation(
        operation_id="op-123",
        operation_type=InstalledOperationType.SYNC.value,
        lease_owner_id="launch-abc",
        lease_duration_seconds=60,
        target_symbols=["2330.TW"],
    )
    assert op.operation_id == "op-123"
    assert op.status == InstalledOperationStatus.RUNNING.value
    assert op.current_stage == "prerequisites_calendar"
    assert op.lease_owner_id == "launch-abc"
    assert "2330.TW" in op.target_symbols_json

    # 2. Get active operation
    active = repository.get_active_operation()
    assert active is not None
    assert active.operation_id == "op-123"

    # 3. Get by ID
    by_id = repository.get_operation_by_id("op-123")
    assert by_id is not None
    assert by_id.operation_id == "op-123"

    # 4. Extend lease
    new_lease = repository.extend_lease("op-123", lease_duration_seconds=120)
    updated = repository.get_operation_by_id("op-123")
    assert updated is not None
    assert updated.lease_expires_at == new_lease

    # 5. Transition stage
    new_lease_2 = repository.transition_stage(
        "op-123", stage=InstalledOperationStage.UNIVERSE.value, lease_duration_seconds=60
    )
    updated_stage = repository.get_operation_by_id("op-123")
    assert updated_stage is not None
    assert updated_stage.current_stage == InstalledOperationStage.UNIVERSE.value

    # 6. Finalize operation
    repository.finalize_operation("op-123", status=InstalledOperationStatus.SUCCEEDED.value)
    finalized = repository.get_operation_by_id("op-123")
    assert finalized is not None
    assert finalized.status == InstalledOperationStatus.SUCCEEDED.value
    assert finalized.completed_at is not None

    # 7. No active operation once finalized
    assert repository.get_active_operation() is None


def test_operation_request_cancel(repo: tuple[InstalledDataOperationsRepository, str]) -> None:
    repository, _ = repo

    repository.create_operation(
        operation_id="op-cancel",
        operation_type=InstalledOperationType.SYNC.value,
        lease_owner_id="launch-123",
    )
    # Cancel running operation
    assert repository.request_cancel("op-cancel") is True
    cancelled = repository.get_operation_by_id("op-cancel")
    assert cancelled is not None
    assert cancelled.status == InstalledOperationStatus.CANCELLED.value or cancelled.status == "cancelling"

    # Cannot cancel already cancelled/terminal operation
    assert repository.request_cancel("op-cancel") is False


def test_item_lineage_tracking(repo: tuple[InstalledDataOperationsRepository, str]) -> None:
    repository, _ = repo

    repository.create_operation(
        operation_id="op-items",
        operation_type=InstalledOperationType.SYNC.value,
        lease_owner_id="launch-123",
    )

    item1 = repository.create_item(
        item_id="item-1",
        operation_id="op-items",
        stage=InstalledOperationStage.PREREQUISITES_CALENDAR.value,
        resource_id="twse.trading-calendar",
    )
    assert item1.status == "running"

    repository.update_item(
        item_id="item-1",
        status=InstalledItemStatus.ACCEPTED.value,
    )

    items = repository.list_items_by_operation("op-items")
    assert len(items) == 1
    assert items[0].item_id == "item-1"
    assert items[0].resource_id == "twse.trading-calendar"
    assert items[0].status == InstalledItemStatus.ACCEPTED.value

    # Test exact Phase 19 capability IDs for universe
    item2 = repository.create_item(
        item_id="item-2",
        operation_id="op-items",
        stage=InstalledOperationStage.UNIVERSE.value,
        resource_id="twse.t187ap03_L",
    )
    assert item2.resource_id == "twse.t187ap03_L"

    item3 = repository.create_item(
        item_id="item-3",
        operation_id="op-items",
        stage=InstalledOperationStage.UNIVERSE.value,
        resource_id="tpex.mopsfin_t187ap03_O",
    )
    assert item3.resource_id == "tpex.mopsfin_t187ap03_O"

    items_all = repository.list_items_by_operation("op-items")
    assert [i.resource_id for i in items_all] == [
        "twse.trading-calendar",
        "twse.t187ap03_L",
        "tpex.mopsfin_t187ap03_O",
    ]
