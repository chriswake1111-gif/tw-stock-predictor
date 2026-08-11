import sqlite3

import pytest

from src.domain.data_foundation import (
    AuthorityTier,
    DataHealthStatus,
    DataProvider,
    DataResource,
    EligibilityStatus,
    ExpectedFrequency,
    IngestionItemStatus,
    IngestionRun,
    IngestionRunItem,
    IngestionRunStatus,
    ProviderType,
    RawResourceRevision,
    ResourceType,
    StoragePolicy,
    TriggerType,
    schema_fingerprint,
    sha256_text,
)
from src.repositories.data_foundation_repository import DataFoundationRepository
from src.services.evidence_backup_service import EvidenceBackupService


NOW = "2026-08-11T00:00:00Z"
SCHEMA = schema_fingerprint(["Date", "TradeValue"])


def foundation(tmp_path):
    repo = DataFoundationRepository(str(tmp_path / "phase10.db"))
    repo.register_provider(DataProvider(
        provider_id="twse", display_name="TWSE",
        authority_tier=AuthorityTier.AUTHORITATIVE,
        provider_type=ProviderType.OFFICIAL,
        base_identity="TWSE official open data", created_at=NOW,
    ))
    repo.register_resource(DataResource(
        resource_id="twse.daily_turnover", provider_id="twse",
        logical_resource_key="twse_daily_turnover",
        resource_type=ResourceType.MARKET_TURNOVER, market="TWSE",
        expected_frequency=ExpectedFrequency.DAILY,
        freshness_policy="official trading-day cadence",
        parser_id="twse_fmtqik", parser_version="1.0.0", schema_version="1",
        storage_policy=StoragePolicy.ARCHIVE_NORMALIZED, created_at=NOW,
    ))
    return repo


def run(run_id="run.phase10-1", status=IngestionRunStatus.RUNNING, completed_at=None):
    return IngestionRun(
        ingestion_run_id=run_id, started_at=NOW,
        trigger_type=TriggerType.MANUAL, runner_version="phase10-v1",
        requested_resources=("twse.daily_turnover",),
        actor_id="internal.phase10-ingestion", status=status,
        completed_at=completed_at,
    )


def raw(revision_id, payload, *, supersedes=None):
    return RawResourceRevision(
        raw_resource_revision_id=revision_id, provider_id="twse",
        resource_id="twse.daily_turnover", logical_revision_key="2026-08-10",
        source_published_at="2026-08-10T06:00:00Z",
        available_at="2026-08-10T06:00:00Z",
        received_at="2026-08-10T06:01:00Z", ingested_at="2026-08-10T06:02:00Z",
        raw_payload_sha256=sha256_text(payload), parser_version="1.0.0",
        schema_fingerprint=SCHEMA,
        storage_policy=StoragePolicy.ARCHIVE_NORMALIZED,
        quality_status=DataHealthStatus.FRESH,
        eligibility_status=EligibilityStatus.ELIGIBLE,
        supersedes_revision_id=supersedes,
    )


def test_registry_run_items_partial_state_and_terminal_transition(tmp_path):
    repo = foundation(tmp_path)
    assert repo.register_provider(DataProvider(
        provider_id="twse", display_name="TWSE",
        authority_tier=AuthorityTier.AUTHORITATIVE,
        provider_type=ProviderType.OFFICIAL,
        base_identity="TWSE official open data", created_at=NOW,
    ))["created"] is False
    repo.add_run(run())
    stored = repo.add_run_item(IngestionRunItem(
        ingestion_run_item_id="item.twse-1", ingestion_run_id="run.phase10-1",
        provider_id="twse", resource_id="twse.daily_turnover",
        started_at=NOW, completed_at="2026-08-11T00:00:01Z",
        status=IngestionItemStatus.PARTIAL,
        quality_status=DataHealthStatus.PARTIAL,
        record_count=1, accepted_count=1,
        raw_payload_sha256=sha256_text("partial"), parser_version="1.0.0",
        schema_fingerprint=SCHEMA, reason="paired resource failed",
    ))
    assert stored["status"] == "partial"
    completed = repo.complete_run(run(
        status=IngestionRunStatus.PARTIAL,
        completed_at="2026-08-11T00:00:02Z",
    ))
    assert completed["status"] == "partial"


def test_raw_revision_is_idempotent_corrected_revision_is_separate_and_immutable(tmp_path):
    repo = foundation(tmp_path)
    first = repo.add_raw_revision(raw("rawrev.first", "payload-a"))
    duplicate = repo.add_raw_revision(raw("rawrev.same-other-id", "payload-a"))
    assert first["created"] is True
    assert duplicate["created"] is False
    assert duplicate["raw_resource_revision_id"] == "rawrev.first"
    with pytest.raises(ValueError, match="explicitly supersede"):
        repo.add_raw_revision(raw("rawrev.corrected-bad", "payload-b"))
    corrected = repo.add_raw_revision(raw(
        "rawrev.corrected", "payload-b", supersedes="rawrev.first"
    ))
    assert corrected["created"] is True
    assert corrected["raw_payload_sha256"] != first["raw_payload_sha256"]
    with sqlite3.connect(repo.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE raw_resource_revisions SET reason='tampered' WHERE raw_resource_revision_id='rawrev.first'"
            )


def test_resource_lock_prevents_concurrent_ingestion(tmp_path):
    repo = foundation(tmp_path)
    repo.add_run(run("run.owner"))
    repo.add_run(run("run.other"))
    repo.acquire_resource_lock("twse.daily_turnover", "run.owner", NOW)
    with pytest.raises(RuntimeError, match="already locked"):
        repo.acquire_resource_lock("twse.daily_turnover", "run.other", NOW)
    with pytest.raises(RuntimeError, match="not owned"):
        repo.release_resource_lock("twse.daily_turnover", "run.other")
    repo.release_resource_lock("twse.daily_turnover", "run.owner")


def test_backup_restore_round_trip_preserves_irreplaceable_and_operational_state(tmp_path):
    repo = foundation(tmp_path)
    repo.add_run(run())
    repo.add_raw_revision(raw("rawrev.first", "payload-a"))
    source = repo.db_path
    backup = tmp_path / "backups" / "evidence.db"
    restored = tmp_path / "restored" / "evidence.db"
    backup_result = EvidenceBackupService.backup(source, str(backup))
    restore_result = EvidenceBackupService.restore(str(backup), str(restored))
    assert backup_result["integrity_check"] == "ok"
    assert restore_result["operational_provenance_counts"] == {
        "data_providers": 1,
        "data_resources": 1,
        "ingestion_runs": 1,
        "ingestion_run_items": 0,
        "raw_resource_revisions": 1,
        "data_quality_issues": 0,
        "trading_calendar_revisions": 0,
        "snapshot_dependency_checks": 0,
    }
    assert set(restore_result["irreplaceable_counts"]) >= {
        "forward_eps_observations", "valuation_approvals", "analysis_snapshots"
    }
