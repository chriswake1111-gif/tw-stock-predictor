ALTER TABLE outcome_resource_manifests
ADD COLUMN outcome_observed_through_session TEXT;

CREATE TABLE evaluation_run_snapshots (
    evaluation_run_id TEXT NOT NULL REFERENCES evaluation_runs(evaluation_run_id),
    snapshot_id TEXT NOT NULL REFERENCES analysis_snapshots(snapshot_id),
    symbol TEXT NOT NULL,
    membership_status TEXT NOT NULL CHECK (
        membership_status IN ('requested','evaluated','no_eligible_subjects')
    ),
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (evaluation_run_id, snapshot_id)
);

CREATE INDEX idx_evaluation_run_snapshots_status
ON evaluation_run_snapshots(evaluation_run_id, membership_status, symbol);

CREATE TRIGGER evaluation_run_snapshots_no_update
BEFORE UPDATE ON evaluation_run_snapshots
BEGIN SELECT RAISE(ABORT, 'evaluation_run_snapshots are immutable'); END;

CREATE TRIGGER evaluation_run_snapshots_no_delete
BEFORE DELETE ON evaluation_run_snapshots
BEGIN SELECT RAISE(ABORT, 'evaluation_run_snapshots are immutable'); END;
