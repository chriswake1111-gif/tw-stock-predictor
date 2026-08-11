ALTER TABLE ingestion_resource_locks ADD COLUMN lease_expires_at TEXT;

UPDATE ingestion_resource_locks
SET lease_expires_at = acquired_at
WHERE lease_expires_at IS NULL;

CREATE TRIGGER ingestion_resource_locks_require_lease_insert
BEFORE INSERT ON ingestion_resource_locks
WHEN NEW.lease_expires_at IS NULL
BEGIN
    SELECT RAISE(ABORT, 'resource lock lease_expires_at is required');
END;

CREATE TRIGGER ingestion_resource_locks_require_lease_update
BEFORE UPDATE ON ingestion_resource_locks
WHEN NEW.lease_expires_at IS NULL
BEGIN
    SELECT RAISE(ABORT, 'resource lock lease_expires_at is required');
END;

CREATE TABLE ingestion_lock_recovery_events (
    recovery_event_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    previous_owner_run_id TEXT NOT NULL REFERENCES ingestion_runs(ingestion_run_id),
    recovering_run_id TEXT NOT NULL REFERENCES ingestion_runs(ingestion_run_id),
    previous_acquired_at TEXT NOT NULL,
    previous_lease_expires_at TEXT NOT NULL,
    recovered_at TEXT NOT NULL,
    previous_run_status TEXT NOT NULL,
    recovery_action TEXT NOT NULL,
    reason TEXT NOT NULL
);

CREATE INDEX idx_ingestion_lock_recovery_resource
ON ingestion_lock_recovery_events(resource_id, recovered_at, recovery_event_id);

CREATE TABLE resource_publication_evidence (
    publication_evidence_id TEXT PRIMARY KEY,
    identity_fingerprint TEXT NOT NULL UNIQUE,
    provider_id TEXT NOT NULL REFERENCES data_providers(provider_id),
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    logical_revision_key TEXT NOT NULL,
    official_release_at TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    evidence_file_sha256 TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    verification_mode TEXT NOT NULL CHECK (
        verification_mode IN ('manual_official_source_review','official_machine_source')
    ),
    verified_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted','revoked')),
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    supersedes_evidence_id TEXT REFERENCES resource_publication_evidence(publication_evidence_id),
    created_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    UNIQUE(provider_id, resource_id, logical_revision_key, revision_number)
);

CREATE INDEX idx_publication_evidence_asof
ON resource_publication_evidence(
    resource_id, logical_revision_key, official_release_at,
    ingested_at, revision_number
);

ALTER TABLE cbc_m1b_monthly
ADD COLUMN publication_evidence_id TEXT REFERENCES resource_publication_evidence(publication_evidence_id);

CREATE TRIGGER ingestion_lock_recovery_events_no_update
BEFORE UPDATE ON ingestion_lock_recovery_events
BEGIN
    SELECT RAISE(ABORT, 'ingestion lock recovery events are immutable');
END;

CREATE TRIGGER ingestion_lock_recovery_events_no_delete
BEFORE DELETE ON ingestion_lock_recovery_events
BEGIN
    SELECT RAISE(ABORT, 'ingestion lock recovery events are immutable');
END;

CREATE TRIGGER resource_publication_evidence_no_update
BEFORE UPDATE ON resource_publication_evidence
BEGIN
    SELECT RAISE(ABORT, 'resource publication evidence is immutable');
END;

CREATE TRIGGER resource_publication_evidence_no_delete
BEFORE DELETE ON resource_publication_evidence
BEGIN
    SELECT RAISE(ABORT, 'resource publication evidence is immutable');
END;
