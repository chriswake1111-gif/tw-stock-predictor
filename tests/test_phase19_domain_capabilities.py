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


def test_readiness_evaluator_coherent_layer1_proof() -> None:
    import sqlite3
    from src.services.installed_readiness_evaluator import evaluate_installed_readiness
    from src.domain.installed_data_operations import InstalledReadiness

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "test.db")
        apply_valuation_migration(db_path)
        conn = sqlite3.connect(db_path)
        try:
            # Empty -> NOT_INITIALIZED
            readiness, details = evaluate_installed_readiness(conn)
            assert readiness == InstalledReadiness.NOT_INITIALIZED

            # Seed calendar on 2026-08-27 (matching EOD date)
            conn.execute(
                """
                INSERT INTO trading_calendar_revisions (
                    calendar_revision_id, raw_resource_revision_id, market, trade_date,
                    session_status, available_at, ingested_at, revision_number, status
                ) VALUES ('c1', 'r1', 'TW', '2026-08-27', 'trading', '2026-08-27', '2026-08-27', 1, 'available')
                """
            )
            # Seed universe instrument
            conn.execute(
                """
                INSERT INTO universe_instruments (
                    instrument_id, venue, official_code, identity_epoch, identity_binding_fingerprint,
                    first_observed_at, first_source_reference, source_identity, display_name, created_at
                ) VALUES ('inst-1', 'TWSE', '2330', 1, 'fp-1', '2026-08-27', 'twse', 'twse:2330', 'TSMC', '2026-08-27')
                """
            )
            conn.execute(
                """
                INSERT INTO universe_lifecycle_events (
                    lifecycle_event_id, instrument_id, event_type, available_at, ingested_at, source_reference, status, reason
                ) VALUES ('l1', 'inst-1', 'listed', '2026-08-27', '2026-08-27', 'twse', 'accepted', 'test')
                """
            )
            conn.execute(
                """
                INSERT INTO universe_operational_state_events (
                    operational_event_id, instrument_id, trading_state, available_at, ingested_at, source_reference, status, reason
                ) VALUES ('o1', 'inst-1', 'normal', '2026-08-27', '2026-08-27', 'twse', 'accepted', 'test')
                """
            )
            # Seed turnover (dual venue)
            conn.execute(
                """
                INSERT INTO market_turnover_daily (
                    id, trade_date, twse_turnover_twd, tpex_turnover_twd, total_turnover_twd,
                    available_at, fetched_at, ingested_at, revision, status
                ) VALUES ('t1', '2026-08-27', 1000.0, 500.0, 1500.0, '2026-08-27', '2026-08-27', '2026-08-27', 1, 'available')
                """
            )
            # Seed EOD close with NO binding to universe instrument (different instrument_id)
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                """
                INSERT INTO eod_close_observations (
                    close_observation_id, resource_id, raw_resource_revision_id, source_snapshot_id,
                    venue, official_code, trade_date, trade_date_status, revision_number,
                    product_scope, observation_status, public_eligibility_status, quality_status,
                    quality_flags_json, row_fingerprint, raw_payload_sha256, normalized_payload_sha256,
                    source_trading_scope, ingested_at, source_record_reference, identity_fingerprint,
                    source_observation_state, instrument_id, close_value
                ) VALUES (
                    'obs-1', 'twse.eod.stock_day_all', 'raw-1', 'snap-1',
                    'TWSE', '2317', '2026-08-27', 'valid', 1,
                    'supported_stock', 'available', 'eligible', 'fresh',
                    '[]', 'rfp-1', 'h1', 'h2',
                    'general', '2026-08-27', 'ref-1', 'ifp-1',
                    'source_observed', 'inst-unbound', '100.0'
                )
                """
            )
            # Unbound observation fails coherent binding -> PARTIAL
            readiness, details = evaluate_installed_readiness(conn)
            assert readiness == InstalledReadiness.PARTIAL

            # Bind observation by registering inst-unbound into universe_instruments with identity_epoch = 1
            conn.execute(
                """
                INSERT INTO universe_instruments (
                    instrument_id, venue, official_code, identity_epoch, identity_binding_fingerprint,
                    first_observed_at, first_source_reference, source_identity, display_name, created_at
                ) VALUES ('inst-unbound', 'TWSE', '2317', 1, 'fp-2', '2026-08-27', 'twse', 'twse:2317', 'HonHai', '2026-08-27')
                """
            )
            # All pillars coherent and calendar matches EOD date -> READY!
            readiness, details = evaluate_installed_readiness(conn)
            assert readiness == InstalledReadiness.READY

            # Append newer trading calendar revision c2 with date 2026-08-28 -> now EOD is behind -> STALE!
            conn.execute(
                """
                INSERT INTO trading_calendar_revisions (
                    calendar_revision_id, raw_resource_revision_id, market, trade_date,
                    session_status, available_at, ingested_at, revision_number, status
                ) VALUES ('c2', 'r2', 'TW', '2026-08-28', 'trading', '2026-08-28', '2026-08-28', 1, 'available')
                """
            )
            readiness, details = evaluate_installed_readiness(conn)
            assert readiness == InstalledReadiness.STALE
        finally:
            conn.close()
