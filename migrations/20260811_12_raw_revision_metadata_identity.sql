DROP TRIGGER raw_resource_revisions_no_update;
DROP TRIGGER raw_resource_revisions_no_delete;

PRAGMA legacy_alter_table = ON;
ALTER TABLE raw_resource_revisions RENAME TO raw_resource_revisions_before_metadata_identity;

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
    UNIQUE(
        provider_id, resource_id, logical_revision_key, raw_payload_sha256,
        source_published_at, available_at, quality_status, eligibility_status
    )
);

INSERT INTO raw_resource_revisions
SELECT * FROM raw_resource_revisions_before_metadata_identity;

DROP TABLE raw_resource_revisions_before_metadata_identity;
PRAGMA legacy_alter_table = OFF;

CREATE INDEX idx_raw_resource_asof
ON raw_resource_revisions(
    resource_id, available_at, ingested_at, logical_revision_key,
    raw_resource_revision_id
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
