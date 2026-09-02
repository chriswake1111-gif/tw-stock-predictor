"""Tests for Phase 19 domain capability model and write guard integration."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.domain.installed_data_operations import (
    INSTALLED_OPERATIONS_ALLOWED_RESOURCES,
    InstalledWriteAuthorization,
    OperationAuthorizationRevoked,
)
from src.domain.valuation import utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration
from src.services.eod_close_ingestion_service import (
    EodCloseIngestionService,
    EodIngestionDisabled,
)
from src.services.universe_write_guard import (
    UniverseIngestionWritesDisabled,
    UniverseOperatorContext,
    UniverseOperatorContextRequired,
    UniverseWriteGuard,
)


def _future_iso(seconds: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _past_iso(seconds: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def test_capability_is_valid_success() -> None:
    auth = InstalledWriteAuthorization(
        operation_id="op-1",
        instance_id="inst-1",
        lease_expires_at=_future_iso(60),
    )
    assert auth.is_valid(current_instance_id="inst-1", resource_id="twse.t187ap03_L") is True
    assert auth.is_valid(current_instance_id="inst-1", resource_id="twse.eod.stock_day_all") is True


def test_capability_fails_on_revocation() -> None:
    auth = InstalledWriteAuthorization(
        operation_id="op-1",
        instance_id="inst-1",
        lease_expires_at=_future_iso(60),
    )
    auth.revoke()
    assert auth.is_valid(current_instance_id="inst-1", resource_id="twse.t187ap03_L") is False


def test_capability_fails_on_instance_mismatch() -> None:
    auth = InstalledWriteAuthorization(
        operation_id="op-1",
        instance_id="inst-1",
        lease_expires_at=_future_iso(60),
    )
    assert auth.is_valid(current_instance_id="inst-2", resource_id="twse.t187ap03_L") is False
    assert auth.is_valid(current_instance_id=None, resource_id="twse.t187ap03_L") is False


def test_capability_fails_on_unapproved_resource() -> None:
    auth = InstalledWriteAuthorization(
        operation_id="op-1",
        instance_id="inst-1",
        lease_expires_at=_future_iso(60),
    )
    assert auth.is_valid(current_instance_id="inst-1", resource_id="unapproved.resource.id") is False
    assert auth.is_valid(current_instance_id="inst-1", resource_id=None) is False


def test_capability_fails_on_expired_lease() -> None:
    auth = InstalledWriteAuthorization(
        operation_id="op-1",
        instance_id="inst-1",
        lease_expires_at=_past_iso(60),
    )
    assert auth.is_valid(current_instance_id="inst-1", resource_id="twse.t187ap03_L") is False


def test_capability_refresh_lease() -> None:
    auth = InstalledWriteAuthorization(
        operation_id="op-1",
        instance_id="inst-1",
        lease_expires_at=_past_iso(60),
    )
    assert auth.is_valid(current_instance_id="inst-1", resource_id="twse.t187ap03_L") is False
    auth.refresh_lease(_future_iso(120))
    assert auth.is_valid(current_instance_id="inst-1", resource_id="twse.t187ap03_L") is True


def test_universe_write_guard_with_capability() -> None:
    auth = InstalledWriteAuthorization(
        operation_id="op-1",
        instance_id="inst-1",
        lease_expires_at=_future_iso(60),
    )
    guard = UniverseWriteGuard(
        enabled=False,
        authorization=auth,
        active_instance_id="inst-1",
    )
    ctx = UniverseOperatorContext(
        actor_id="installed_user",
        run_id="run-1",
        lock_id="lock-1",
        audit_id="audit-1",
    )
    # Valid capability allows mutation
    result = guard.require_enabled(context=ctx, resource_id="twse.t187ap03_L")
    assert result.actor_id == "installed_user"

    # Missing context raises required error
    with pytest.raises(UniverseOperatorContextRequired):
        guard.require_enabled(context=None, resource_id="twse.t187ap03_L")

    # Incomplete context raises required error
    with pytest.raises(UniverseOperatorContextRequired):
        guard.require_enabled(
            context=UniverseOperatorContext(actor_id="installed_user", run_id="", lock_id="lock-1", audit_id="audit-1"),
            resource_id="twse.t187ap03_L",
        )

    # Wrong resource raises disabled
    with pytest.raises(UniverseIngestionWritesDisabled):
        guard.require_enabled(context=ctx, resource_id="unauthorized.resource")

    # Revoked capability raises disabled
    auth.revoke()
    with pytest.raises(UniverseIngestionWritesDisabled):
        guard.require_enabled(context=ctx, resource_id="twse.t187ap03_L")


def test_universe_write_guard_legacy_enabled() -> None:
    guard = UniverseWriteGuard(enabled=True)
    ctx = UniverseOperatorContext(
        actor_id="phase13-operator",
        run_id="run-1",
        lock_id="lock-1",
        audit_id="audit-1",
    )
    result = guard.require_enabled(context=ctx)
    assert result.actor_id == "phase13-operator"


def test_eod_close_ingestion_service_with_capability() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "test.db")
        apply_valuation_migration(db_path)

        auth = InstalledWriteAuthorization(
            operation_id="op-1",
            instance_id="inst-1",
            lease_expires_at=_future_iso(60),
        )
        service = EodCloseIngestionService(
            db_path=db_path,
            enabled=False,
            authorization=auth,
            active_instance_id="inst-1",
        )

        # _require_enabled succeeds for authorized resource
        service._require_enabled(resource_id="twse.eod.stock_day_all")
        service._require_enabled(resource_id="twse.isin.security_classification")

        # _require_enabled fails for unauthorized resource
        with pytest.raises(EodIngestionDisabled):
            service._require_enabled(resource_id="unauthorized.resource")

        # _require_enabled fails on instance mismatch
        with pytest.raises(EodIngestionDisabled):
            service._require_enabled(
                resource_id="twse.eod.stock_day_all",
                current_instance_id="wrong-inst",
            )

        # _require_enabled fails when revoked
        auth.revoke()
        with pytest.raises(EodIngestionDisabled):
            service._require_enabled(resource_id="twse.eod.stock_day_all")


def test_eod_close_ingestion_service_legacy_enabled() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "test.db")
        apply_valuation_migration(db_path)

        service = EodCloseIngestionService(db_path=db_path, enabled=True)
        # Succeeds without capability
        service._require_enabled()
