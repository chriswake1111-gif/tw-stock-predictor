-- Phase 14 Third Code Review remediation.
-- Keep command identity separate from content identity.  This is a
-- data-preserving, versioned rebuild of the two Phase 14 idempotency tables;
-- migrations 18 and 19 remain immutable.

CREATE TABLE eod_ingestion_idempotency_v20 (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    raw_resource_revision_id TEXT NOT NULL REFERENCES raw_resource_revisions(raw_resource_revision_id),
    source_snapshot_id TEXT REFERENCES eod_close_source_snapshots(source_snapshot_id),
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO eod_ingestion_idempotency_v20 (
    idempotency_key, payload_fingerprint, resource_id,
    raw_resource_revision_id, source_snapshot_id, actor_id, created_at
)
SELECT
    idempotency_key, payload_fingerprint, resource_id,
    raw_resource_revision_id, source_snapshot_id, actor_id, created_at
FROM eod_ingestion_idempotency;

DROP TABLE eod_ingestion_idempotency;
ALTER TABLE eod_ingestion_idempotency_v20 RENAME TO eod_ingestion_idempotency;

CREATE INDEX idx_eod_ingestion_idempotency_resource
ON eod_ingestion_idempotency(resource_id, payload_fingerprint);

CREATE INDEX idx_eod_ingestion_idempotency_content_scope
ON eod_ingestion_idempotency(resource_id, raw_resource_revision_id, payload_fingerprint);

CREATE TRIGGER eod_ingestion_idempotency_no_update
BEFORE UPDATE ON eod_ingestion_idempotency
BEGIN
    SELECT RAISE(ABORT, 'eod ingestion idempotency is immutable');
END;

CREATE TRIGGER eod_ingestion_idempotency_no_delete
BEFORE DELETE ON eod_ingestion_idempotency
BEGIN
    SELECT RAISE(ABORT, 'eod ingestion idempotency is immutable');
END;

CREATE TABLE eod_ingestion_command_reservations_v20 (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    actor_id TEXT NOT NULL,
    command_received_at TEXT NOT NULL,
    source_published_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('reserved','running','completed')),
    run_id TEXT REFERENCES ingestion_runs(ingestion_run_id),
    lock_id TEXT,
    audit_id TEXT,
    result_json TEXT,
    reserved_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT,
    CHECK (status <> 'completed' OR result_json IS NOT NULL),
    CHECK (status = 'reserved' OR run_id IS NOT NULL)
);

INSERT INTO eod_ingestion_command_reservations_v20 (
    idempotency_key, payload_fingerprint, resource_id, actor_id,
    command_received_at, source_published_at, status, run_id, lock_id,
    audit_id, result_json, reserved_at, updated_at, last_error
)
SELECT
    idempotency_key, payload_fingerprint, resource_id, actor_id,
    command_received_at, source_published_at, status, run_id, lock_id,
    audit_id, result_json, reserved_at, updated_at, last_error
FROM eod_ingestion_command_reservations;

DROP TABLE eod_ingestion_command_reservations;
ALTER TABLE eod_ingestion_command_reservations_v20
RENAME TO eod_ingestion_command_reservations;

CREATE INDEX idx_eod_command_reservations_resource_status
ON eod_ingestion_command_reservations(resource_id, status, updated_at);

CREATE INDEX idx_eod_command_reservations_content_scope
ON eod_ingestion_command_reservations(resource_id, payload_fingerprint);

CREATE TRIGGER eod_command_reservations_valid_transition
BEFORE UPDATE ON eod_ingestion_command_reservations
WHEN NOT (
    (OLD.status = NEW.status)
    OR (OLD.status = 'reserved' AND NEW.status = 'running')
    OR (OLD.status = 'running' AND NEW.status IN ('reserved','completed'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid EOD ingestion command reservation transition');
END;
