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
    SnapshotFreshnessResult,
    SnapshotFreshnessStatus,
    StoragePolicy,
    TriggerType,
    schema_fingerprint,
    sha256_text,
)


NOW = "2026-08-11T00:00:00Z"
HASH = sha256_text("official fixture")
SCHEMA = schema_fingerprint(["Date", "TradeValue"])


def test_provider_and_resource_identity_are_canonical_and_url_independent():
    provider = DataProvider(
        provider_id="twse",
        display_name="Taiwan Stock Exchange",
        authority_tier=AuthorityTier.AUTHORITATIVE,
        provider_type=ProviderType.OFFICIAL,
        base_identity="TWSE official open data",
        created_at=NOW,
    ).canonical_payload()
    resource = DataResource(
        resource_id="twse.daily_turnover",
        provider_id=provider["provider_id"],
        logical_resource_key="twse_daily_turnover",
        resource_type=ResourceType.MARKET_TURNOVER,
        market="twse",
        expected_frequency=ExpectedFrequency.DAILY,
        freshness_policy="official trading-day cadence",
        parser_id="twse_fmtqik",
        parser_version="1.0.0",
        schema_version="1",
        storage_policy=StoragePolicy.ARCHIVE_NORMALIZED,
        created_at=NOW,
    ).canonical_payload()
    assert provider["provider_type"] == "official"
    assert resource["resource_id"] == "twse.daily_turnover"
    assert "url" not in resource


def test_ingestion_run_requires_explicit_non_human_runner_identity():
    run = IngestionRun(
        ingestion_run_id="run.phase10-001",
        started_at=NOW,
        trigger_type=TriggerType.MANUAL,
        runner_version="phase10-v1",
        requested_resources=("tpex.daily_turnover", "twse.daily_turnover"),
        actor_id="internal.phase10-ingestion",
    ).canonical_payload()
    assert run["requested_resources"] == [
        "tpex.daily_turnover",
        "twse.daily_turnover",
    ]
    assert run["status"] == "running"
    with pytest.raises(ValueError, match="completed ingestion"):
        IngestionRun(
            ingestion_run_id="run.bad",
            started_at=NOW,
            trigger_type=TriggerType.MANUAL,
            runner_version="v1",
            requested_resources=("twse.daily_turnover",),
            actor_id="internal.runner",
            status=IngestionRunStatus.SUCCEEDED,
        ).canonical_payload()


def test_per_resource_item_preserves_provider_failure_independently():
    item = IngestionRunItem(
        ingestion_run_item_id="item.tpex-001",
        ingestion_run_id="run.phase10-001",
        provider_id="tpex",
        resource_id="tpex.daily_turnover",
        started_at=NOW,
        completed_at="2026-08-11T00:00:01Z",
        status=IngestionItemStatus.PROVIDER_ERROR,
        quality_status=DataHealthStatus.PROVIDER_ERROR,
        reason="provider timeout",
    ).canonical_payload()
    assert item["status"] == "provider_error"
    assert item["accepted_count"] == 0


def test_raw_revision_identity_is_deterministic_and_eligibility_is_separate():
    kwargs = dict(
        raw_resource_revision_id="rawrev.source-001",
        provider_id="twse",
        resource_id="twse.daily_turnover",
        logical_revision_key="2026-08-10",
        source_published_at="2026-08-10T06:00:00Z",
        available_at="2026-08-10T06:00:00Z",
        received_at="2026-08-10T06:01:00Z",
        ingested_at="2026-08-10T06:02:00Z",
        raw_payload_sha256=HASH,
        parser_version="1.0.0",
        schema_fingerprint=SCHEMA,
        storage_policy=StoragePolicy.ARCHIVE_NORMALIZED,
        quality_status=DataHealthStatus.FRESH,
        eligibility_status=EligibilityStatus.ELIGIBLE,
    )
    first = RawResourceRevision(**kwargs)
    second = RawResourceRevision(**{**kwargs, "raw_resource_revision_id": "rawrev.other"})
    assert first.deterministic_identity() == second.deterministic_identity()
    assert first.canonical_payload()["eligibility_status"] == "eligible"


def test_missing_publication_metadata_can_be_stored_only_as_awaiting_review():
    candidate = RawResourceRevision(
        raw_resource_revision_id="rawrev.cbc-2026-06",
        provider_id="cbc",
        resource_id="cbc.m1b",
        logical_revision_key="2026-06",
        received_at="2026-07-20T08:00:00Z",
        ingested_at="2026-07-20T08:01:00Z",
        raw_payload_sha256=HASH,
        parser_version="1.0.0",
        schema_fingerprint=SCHEMA,
        storage_policy=StoragePolicy.HASH_ONLY,
        quality_status=DataHealthStatus.AWAITING_REVIEW,
        eligibility_status=EligibilityStatus.AWAITING_REVIEW,
        reason="authoritative publication timestamp required",
    ).canonical_payload()
    assert candidate["available_at"] is None
    assert candidate["eligibility_status"] == "awaiting_review"
    with pytest.raises(ValueError, match="requires available_at"):
        RawResourceRevision(
            **{
                **candidate,
                "quality_status": DataHealthStatus.FRESH,
                "eligibility_status": EligibilityStatus.ELIGIBLE,
                "storage_policy": StoragePolicy.HASH_ONLY,
            }
        ).canonical_payload()


def test_snapshot_freshness_states_do_not_change_snapshot_validity():
    stale = SnapshotFreshnessResult(
        snapshot_id="snapshot-1",
        comparison_cutoff="2026-08-11T00:00:00Z",
        checked_at="2026-08-11T00:00:01Z",
        freshness_status=SnapshotFreshnessStatus.STALE,
        reasons=("new_liquidity_observation_available", "new_forward_eps_available"),
    ).canonical_payload()
    assert stale["freshness_status"] == "stale"
    assert stale["snapshot_validity"] == "immutable_historical_evidence_unchanged"
    assert stale["reasons"] == sorted(stale["reasons"])
    current = SnapshotFreshnessResult(
        snapshot_id="snapshot-1",
        comparison_cutoff=NOW,
        checked_at=NOW,
        freshness_status=SnapshotFreshnessStatus.CURRENT,
    ).canonical_payload()
    assert current["reasons"] == []
    with pytest.raises(ValueError, match="requires at least one reason"):
        SnapshotFreshnessResult(
            snapshot_id="snapshot-1",
            comparison_cutoff=NOW,
            checked_at=NOW,
            freshness_status=SnapshotFreshnessStatus.UNKNOWN,
        ).canonical_payload()
