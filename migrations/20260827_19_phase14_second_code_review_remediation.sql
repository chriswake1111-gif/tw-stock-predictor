-- Phase 14 Second Code Review remediation.
-- Keep source observation evidence separate from public numeric eligibility and
-- add a mutable, durable operator-command state machine.  Migration 18
-- remains immutable history; this migration is additive only.

ALTER TABLE eod_close_observations
ADD COLUMN source_observation_state TEXT NOT NULL DEFAULT 'source_observed'
CHECK (source_observation_state = 'source_observed');

CREATE TABLE eod_ingestion_command_reservations (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL UNIQUE,
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

CREATE INDEX idx_eod_command_reservations_resource_status
ON eod_ingestion_command_reservations(resource_id, status, updated_at);

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
