"""Installed refresh: real daily activity proof, temporal boundaries and bounded retries."""
import hashlib
import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from src.collectors.installed_egress_client import DeadlineExhaustedError
from src.domain.installed_data_operations import InstalledReadiness, OperationAuthorizationRevoked
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.repositories.migration_runner import apply_valuation_migration
from src.services.installed_data_sync_service import InstalledDataSyncService
from src.services.research_bootstrap_service import ResearchBootstrapService
from tests.test_phase20_research_bootstrap_orchestrator import _setup_db, _insert_snapshot_and_observation


@pytest.fixture
def service(tmp_path):
    db = str(tmp_path / "refresh.db")
    apply_valuation_migration(db)
    return InstalledDataSyncService(db, egress_client=MagicMock(), runtime_instance_id="refresh-test")


def activity(venue="TWSE", amount="997944843239", day="1150907"):
    return json.dumps([{"Date": day, "TradeAmount" if venue == "TPEX" else "TradeValue": amount}]).encode()


@pytest.mark.parametrize("venue", ["TWSE", "TPEX"])
def test_missing_regular_session_gets_venue_scoped_observed_proof(service, venue):
    body = activity(venue)
    service.egress_client.fetch.return_value = (200, body, {})
    op, auth = service.create_operation_and_capability()
    service.ensure_official_session(op, auth, venue, "2026-09-07")
    foundation = DataFoundationRepository(service.db_path)
    proof = foundation.calendar_session_as_of(venue, "2026-09-07", "2099-01-01T00:00:00Z")
    assert proof["session_status"] == "trading"
    assert proof["available_at"] > "2026-09-07T00:00:00Z"
    assert foundation.calendar_session_as_of(venue, "2026-09-07", "2026-09-07T00:00:00Z") is None
    assert foundation.calendar_session_as_of("TPEX" if venue == "TWSE" else "TWSE", "2026-09-07", "2099-01-01T00:00:00Z") is None
    with sqlite3.connect(service.db_path) as conn:
        raw = conn.execute("SELECT raw_payload_sha256, resource_id, reason FROM raw_resource_revisions WHERE raw_resource_revision_id=?", (proof["raw_resource_revision_id"],)).fetchone()
    assert raw[0] == hashlib.sha256(body).hexdigest()
    assert raw[1] == f"{venue.lower()}.market-turnover"
    assert "https://" in raw[2]
    service.egress_client.fetch.reset_mock()
    service.ensure_official_session(op, auth, venue, "2026-09-07")
    service.egress_client.fetch.assert_not_called()


@pytest.mark.parametrize("body", [b"[]", b"<html>blocked</html>", activity(amount="0"), activity(amount="NaN"), activity(amount="Infinity"), activity(day="1150904"), b'[{"Date":"1150907"}]', b'[{"Date":"1150907","TradeValue":"1"},{"Date":"1150907","TradeValue":"1"}]'])
def test_bad_or_unrelated_activity_never_creates_session(service, body):
    service.egress_client.fetch.return_value = (200, body, {})
    op, auth = service.create_operation_and_capability()
    with pytest.raises(ValueError, match="calendar proof missing"):
        service.ensure_official_session(op, auth, "TWSE", "2026-09-07")
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM trading_calendar_revisions").fetchone()[0] == 0
    assert service.operation_repo.list_items_by_operation(op)[0].status == "partial"


@pytest.mark.parametrize("status,session", [("revoked", "trading"), ("available", "holiday")])
def test_latest_revocation_or_closure_is_not_overridden(service, status, session):
    service.egress_client.fetch.return_value = (200, activity(), {})
    op, auth = service.create_operation_and_capability()
    service.ensure_official_session(op, auth, "TWSE", "2026-09-07")
    foundation = DataFoundationRepository(service.db_path)
    proof = foundation.calendar_session_as_of("TWSE", "2026-09-07", "2099-01-01T00:00:00Z")
    foundation.add_calendar_revision(calendar_revision_id="revoked-test", raw_resource_revision_id=proof["raw_resource_revision_id"], market="TWSE", trade_date="2026-09-07", session_status=session, available_at=proof["available_at"], ingested_at=proof["ingested_at"], status=status)
    service.egress_client.fetch.reset_mock()
    with pytest.raises(ValueError, match="conflict or revoked"):
        service.ensure_official_session(op, auth, "TWSE", "2026-09-07")
    service.egress_client.fetch.assert_not_called()


def test_cancellation_during_fetch_prevents_calendar_write(service):
    op, auth = service.create_operation_and_capability()
    def fetch(*args, **kwargs):
        auth.revoke()
        return 200, activity(), {}
    service.egress_client.fetch.side_effect = fetch
    with pytest.raises(OperationAuthorizationRevoked):
        service.ensure_official_session(op, auth, "TWSE", "2026-09-07")
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM trading_calendar_revisions").fetchone()[0] == 0


def test_transport_timeout_preserves_missing_state(service):
    op, auth = service.create_operation_and_capability()
    service.egress_client.fetch.side_effect = DeadlineExhaustedError("deadline exhausted")
    with pytest.raises(DeadlineExhaustedError):
        service.ensure_official_session(op, auth, "TWSE", "2026-09-07", 123.0)
    assert service.egress_client.fetch.call_args.kwargs["deadline_monotonic"] == 123.0
    with sqlite3.connect(service.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM trading_calendar_revisions").fetchone()[0] == 0


def test_explicit_refresh_does_not_accept_old_local_price(tmp_path):
    db, current, ops = _setup_db(tmp_path)
    with sqlite3.connect(db) as conn:
        _insert_snapshot_and_observation(conn)
    calls = []
    def runner(op, auth, deadline):
        calls.append(op)
        ops.finalize_operation(op, status="succeeded")
    svc = ResearchBootstrapService(str(db), current_research_service=current, operations_repo=ops, runner_fn=runner)
    assert svc.bootstrap_symbol("2330.TW")["status"] == "ready"
    result = svc.bootstrap_symbol("2330.TW", refresh=True)
    for worker in svc.worker_threads:
        worker.join(5)
    assert result["status"] == "preparing"
    assert calls == [result["operation_id"]]


def test_projection_keeps_phase16_degradation_partial_when_readiness_is_ready(service, monkeypatch):
    """A valid EOD row must not turn an unresolved Phase 16 queue into success."""
    monkeypatch.setattr(
        "src.services.installed_data_sync_service.evaluate_installed_readiness",
        lambda conn: (InstalledReadiness.READY, {}),
    )
    op, auth = service.create_operation_and_capability()

    service.run_stage_projection(
        op,
        auth,
        partial_reason="symbol 2330.TW Phase16 context is identity_unresolved",
    )

    row = service.operation_repo.get_operation_by_id(op)
    assert row is not None
    assert row.status == "partial"
    assert row.error_detail == "symbol 2330.TW Phase16 context is identity_unresolved"


def test_phase16_enablement_quality_reports_unresolved_item(service, monkeypatch):
    """The target item state and reason are propagated without fabricating data."""
    class FakeProjection:
        items = (
            {
                "canonical_symbol": "2330.TW",
                "item_kind": "denominator_candidate",
                "item_state": "identity_unresolved",
                "reason_codes": ["identity_unresolved", "publication_instant_unproven"],
            },
        )

    class FakeRepository:
        def __init__(self, db_path, *, storage):
            self.storage = storage

        def read_for_symbols_with_connection(self, conn, request, *, canonical_symbols):
            assert canonical_symbols == ["2330.TW"]
            return FakeProjection()

    monkeypatch.setattr(
        "src.services.installed_data_sync_service.NeutralBatchMarketContextRepository",
        FakeRepository,
    )

    state, reason = service._phase16_enablement_quality(
        canonical_symbol="2330.TW",
        market_date="2026-09-07",
        knowledge_cutoff_at="2026-09-08T00:00:00Z",
    )

    assert state == "identity_unresolved"
    assert reason is not None
    assert "publication_instant_unproven" in reason
