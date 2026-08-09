CREATE TABLE evaluation_profile_revisions (
    id TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_profile_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    previous_revision_id TEXT REFERENCES evaluation_profile_revisions(id),
    horizons_sessions_json TEXT NOT NULL,
    start_policy TEXT NOT NULL,
    start_price_policy TEXT NOT NULL,
    end_price_policy TEXT NOT NULL,
    target_touch_policy TEXT NOT NULL,
    already_in_range_policy TEXT NOT NULL,
    benchmark_policy TEXT NOT NULL,
    outcome_completeness_policy TEXT NOT NULL,
    calculation_quantum TEXT NOT NULL,
    display_quantum TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('available','revoked')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    UNIQUE(logical_profile_id, revision_number)
);

CREATE INDEX idx_evaluation_profile_asof
ON evaluation_profile_revisions(logical_profile_id, available_at, ingested_at, revision_number);

CREATE TABLE evaluation_profile_approvals (
    approval_event_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    profile_revision_id TEXT NOT NULL REFERENCES evaluation_profile_revisions(id),
    decision TEXT NOT NULL CHECK (decision IN ('approved','revoked')),
    approved_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX idx_evaluation_profile_approval_asof
ON evaluation_profile_approvals(profile_revision_id, approved_at, ingested_at);

CREATE TABLE outcome_resource_manifests (
    manifest_id TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    manifest_version INTEGER NOT NULL CHECK (manifest_version >= 1),
    dataset_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    dataset_hash TEXT NOT NULL,
    date_start TEXT NOT NULL,
    date_end TEXT NOT NULL,
    universe_definition TEXT NOT NULL,
    calendar_resource_json TEXT NOT NULL,
    calendar_hash TEXT NOT NULL,
    benchmark_resource_json TEXT NOT NULL,
    benchmark_hash TEXT NOT NULL,
    ohlc_adjustment_contract TEXT NOT NULL,
    corporate_action_contract TEXT NOT NULL,
    symbol_resources_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE evaluation_runs (
    evaluation_run_id TEXT PRIMARY KEY,
    evaluation_profile_revision_id TEXT NOT NULL REFERENCES evaluation_profile_revisions(id),
    evaluator_version TEXT NOT NULL,
    evaluation_origin_policy TEXT NOT NULL,
    snapshot_set_hash TEXT NOT NULL,
    outcome_resource_manifest_id TEXT NOT NULL REFERENCES outcome_resource_manifests(manifest_id),
    outcome_manifest_hash TEXT NOT NULL,
    universe_definition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    run_fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status = 'completed')
);

CREATE TABLE scenario_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    evaluation_run_id TEXT NOT NULL REFERENCES evaluation_runs(evaluation_run_id),
    snapshot_id TEXT NOT NULL REFERENCES analysis_snapshots(snapshot_id),
    symbol TEXT NOT NULL,
    evaluation_origin TEXT NOT NULL CHECK (evaluation_origin IN ('prospective_snapshot','historical_reconstruction')),
    subject_type TEXT NOT NULL CHECK (subject_type IN ('target_candidate','support_candidate','target_cluster')),
    subject_id TEXT NOT NULL,
    method_family TEXT NOT NULL,
    semantic_role TEXT NOT NULL CHECK (semantic_role IN ('target','support')),
    evidence_strength TEXT,
    subject_metadata_json TEXT NOT NULL,
    knowledge_cutoff_at TEXT NOT NULL,
    horizon_sessions INTEGER NOT NULL CHECK (horizon_sessions IN (20,60)),
    evaluation_start_session TEXT,
    evaluation_end_session TEXT,
    market_sessions_skipped INTEGER,
    start_price TEXT,
    target_low TEXT,
    target_high TEXT,
    target_position_at_start TEXT,
    target_reached INTEGER,
    first_target_reached_at TEXT,
    trading_sessions_to_target INTEGER,
    maximum_upside_excursion TEXT,
    maximum_downside_excursion TEXT,
    directional_mfe TEXT,
    directional_mae TEXT,
    forward_return TEXT,
    benchmark_return TEXT,
    excess_return TEXT,
    terminal_outcome TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    benchmark_status TEXT NOT NULL,
    invalidation_status TEXT NOT NULL,
    invalidation_reason TEXT NOT NULL,
    outcome_resource_manifest_id TEXT NOT NULL REFERENCES outcome_resource_manifests(manifest_id),
    calculation_fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(evaluation_run_id, snapshot_id, subject_type, subject_id, horizon_sessions)
);

CREATE INDEX idx_scenario_evaluations_run
ON scenario_evaluations(evaluation_run_id, evaluation_origin, horizon_sessions, subject_type);

CREATE INDEX idx_scenario_evaluations_snapshot
ON scenario_evaluations(snapshot_id, created_at);

CREATE TABLE evaluation_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('profile_revision','profile_approval','outcome_manifest','evaluation_run')),
    resource_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);

CREATE TRIGGER evaluation_profile_revisions_no_update BEFORE UPDATE ON evaluation_profile_revisions
BEGIN SELECT RAISE(ABORT, 'evaluation_profile_revisions are immutable'); END;
CREATE TRIGGER evaluation_profile_revisions_no_delete BEFORE DELETE ON evaluation_profile_revisions
BEGIN SELECT RAISE(ABORT, 'evaluation_profile_revisions are immutable'); END;
CREATE TRIGGER evaluation_profile_approvals_no_update BEFORE UPDATE ON evaluation_profile_approvals
BEGIN SELECT RAISE(ABORT, 'evaluation_profile_approvals are immutable'); END;
CREATE TRIGGER evaluation_profile_approvals_no_delete BEFORE DELETE ON evaluation_profile_approvals
BEGIN SELECT RAISE(ABORT, 'evaluation_profile_approvals are immutable'); END;
CREATE TRIGGER outcome_resource_manifests_no_update BEFORE UPDATE ON outcome_resource_manifests
BEGIN SELECT RAISE(ABORT, 'outcome_resource_manifests are immutable'); END;
CREATE TRIGGER outcome_resource_manifests_no_delete BEFORE DELETE ON outcome_resource_manifests
BEGIN SELECT RAISE(ABORT, 'outcome_resource_manifests are immutable'); END;
CREATE TRIGGER evaluation_runs_no_update BEFORE UPDATE ON evaluation_runs
BEGIN SELECT RAISE(ABORT, 'evaluation_runs are immutable'); END;
CREATE TRIGGER evaluation_runs_no_delete BEFORE DELETE ON evaluation_runs
BEGIN SELECT RAISE(ABORT, 'evaluation_runs are immutable'); END;
CREATE TRIGGER scenario_evaluations_no_update BEFORE UPDATE ON scenario_evaluations
BEGIN SELECT RAISE(ABORT, 'scenario_evaluations are immutable'); END;
CREATE TRIGGER scenario_evaluations_no_delete BEFORE DELETE ON scenario_evaluations
BEGIN SELECT RAISE(ABORT, 'scenario_evaluations are immutable'); END;
