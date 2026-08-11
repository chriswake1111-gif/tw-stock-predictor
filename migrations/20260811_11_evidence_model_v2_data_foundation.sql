CREATE TABLE data_providers (
    provider_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    authority_tier TEXT NOT NULL CHECK (
        authority_tier IN ('authoritative','professional','aggregator','manual_research')
    ),
    provider_type TEXT NOT NULL CHECK (
        provider_type IN ('official','aggregator','manual','internal')
    ),
    base_identity TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE
);

CREATE TABLE data_resources (
    resource_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES data_providers(provider_id),
    logical_resource_key TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (
        resource_type IN (
            'market_turnover','monetary_statistic','trading_calendar',
            'symbol_master','corporate_action'
        )
    ),
    market TEXT NOT NULL,
    expected_frequency TEXT NOT NULL CHECK (
        expected_frequency IN ('daily','monthly_publication','periodic','manual')
    ),
    freshness_policy TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    storage_policy TEXT NOT NULL CHECK (
        storage_policy IN ('archive_raw','archive_normalized','hash_only')
    ),
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    UNIQUE(provider_id, logical_resource_key)
);

CREATE TABLE ingestion_runs (
    ingestion_run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual','scheduled','retry')),
    runner_version TEXT NOT NULL,
    requested_resources_json TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('running','succeeded','partial','failed','blocked')
    ),
    retry_of_run_id TEXT REFERENCES ingestion_runs(ingestion_run_id),
    CHECK (
        (status = 'running' AND completed_at IS NULL) OR
        (status != 'running' AND completed_at IS NOT NULL)
    )
);

CREATE INDEX idx_ingestion_runs_started
ON ingestion_runs(started_at, ingestion_run_id);

CREATE TABLE ingestion_run_items (
    ingestion_run_item_id TEXT PRIMARY KEY,
    ingestion_run_id TEXT NOT NULL REFERENCES ingestion_runs(ingestion_run_id),
    provider_id TEXT NOT NULL REFERENCES data_providers(provider_id),
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'fetched','validated','accepted','partial','provider_error',
            'schema_changed','awaiting_review','quality_warning','rejected'
        )
    ),
    http_status INTEGER,
    raw_payload_sha256 TEXT,
    parser_version TEXT,
    schema_fingerprint TEXT,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    accepted_count INTEGER NOT NULL CHECK (accepted_count >= 0),
    rejected_count INTEGER NOT NULL CHECK (rejected_count >= 0),
    quality_status TEXT NOT NULL CHECK (
        quality_status IN (
            'fresh','stale','missing','partial','provider_error','schema_changed',
            'awaiting_review','quality_warning','rejected','unknown'
        )
    ),
    reason TEXT
);

CREATE INDEX idx_ingestion_run_items_resource
ON ingestion_run_items(resource_id, started_at, ingestion_run_item_id);

CREATE TABLE raw_resource_revisions (
    raw_resource_revision_id TEXT PRIMARY KEY,
    identity_fingerprint TEXT NOT NULL UNIQUE,
    provider_id TEXT NOT NULL REFERENCES data_providers(provider_id),
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    logical_revision_key TEXT NOT NULL,
    source_published_at TEXT,
    available_at TEXT,
    received_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    storage_policy TEXT NOT NULL CHECK (
        storage_policy IN ('archive_raw','archive_normalized','hash_only')
    ),
    storage_location TEXT,
    quality_status TEXT NOT NULL CHECK (
        quality_status IN (
            'fresh','stale','missing','partial','provider_error','schema_changed',
            'awaiting_review','quality_warning','rejected','unknown'
        )
    ),
    eligibility_status TEXT NOT NULL CHECK (
        eligibility_status IN ('eligible','ineligible','awaiting_review')
    ),
    supersedes_revision_id TEXT REFERENCES raw_resource_revisions(raw_resource_revision_id),
    reason TEXT,
    UNIQUE(provider_id, resource_id, logical_revision_key, raw_payload_sha256)
);

CREATE INDEX idx_raw_resource_asof
ON raw_resource_revisions(
    resource_id, available_at, ingested_at, logical_revision_key,
    raw_resource_revision_id
);

CREATE TABLE data_quality_issues (
    data_quality_issue_id TEXT PRIMARY KEY,
    ingestion_run_item_id TEXT REFERENCES ingestion_run_items(ingestion_run_item_id),
    raw_resource_revision_id TEXT REFERENCES raw_resource_revisions(raw_resource_revision_id),
    provider_id TEXT NOT NULL REFERENCES data_providers(provider_id),
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    severity TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
    issue_code TEXT NOT NULL,
    details_json TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX idx_data_quality_resource
ON data_quality_issues(resource_id, detected_at, data_quality_issue_id);

CREATE TABLE trading_calendar_revisions (
    calendar_revision_id TEXT PRIMARY KEY,
    raw_resource_revision_id TEXT NOT NULL REFERENCES raw_resource_revisions(raw_resource_revision_id),
    market TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    session_status TEXT NOT NULL CHECK (
        session_status IN ('trading','holiday','no_trading','special')
    ),
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    status TEXT NOT NULL CHECK (status IN ('available','revoked')),
    note TEXT,
    UNIQUE(market, trade_date, revision_number)
);

CREATE INDEX idx_trading_calendar_asof
ON trading_calendar_revisions(market, trade_date, available_at, ingested_at, revision_number);

CREATE TABLE snapshot_dependency_checks (
    dependency_check_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES analysis_snapshots(snapshot_id),
    comparison_cutoff TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    freshness_status TEXT NOT NULL CHECK (
        freshness_status IN ('current','stale','unknown','blocked')
    ),
    reasons_json TEXT NOT NULL,
    checked_dependencies_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL UNIQUE
);

CREATE INDEX idx_snapshot_dependency_checks
ON snapshot_dependency_checks(snapshot_id, comparison_cutoff, checked_at);

CREATE TABLE ingestion_resource_locks (
    resource_id TEXT PRIMARY KEY REFERENCES data_resources(resource_id),
    owner_run_id TEXT NOT NULL REFERENCES ingestion_runs(ingestion_run_id),
    acquired_at TEXT NOT NULL
);

CREATE TRIGGER raw_resource_revisions_no_update
BEFORE UPDATE ON raw_resource_revisions
BEGIN
    SELECT RAISE(ABORT, 'raw resource revisions are immutable');
END;

CREATE TRIGGER raw_resource_revisions_no_delete
BEFORE DELETE ON raw_resource_revisions
BEGIN
    SELECT RAISE(ABORT, 'raw resource revisions are immutable');
END;

CREATE TRIGGER trading_calendar_revisions_no_update
BEFORE UPDATE ON trading_calendar_revisions
BEGIN
    SELECT RAISE(ABORT, 'trading calendar revisions are immutable');
END;

CREATE TRIGGER trading_calendar_revisions_no_delete
BEFORE DELETE ON trading_calendar_revisions
BEGIN
    SELECT RAISE(ABORT, 'trading calendar revisions are immutable');
END;
