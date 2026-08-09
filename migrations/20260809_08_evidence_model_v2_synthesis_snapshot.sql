CREATE TABLE synthesis_profile_revisions (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_profile_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    revision_of TEXT REFERENCES synthesis_profile_revisions(id),
    scope TEXT NOT NULL CHECK (scope IN ('global','symbol')),
    scope_value TEXT,
    allowed_method_families_json TEXT NOT NULL,
    role_compatibility_policy TEXT NOT NULL,
    point_to_range_policy TEXT NOT NULL,
    overlap_tolerance TEXT NOT NULL,
    boundary_policy TEXT NOT NULL,
    cluster_policy TEXT NOT NULL,
    dependency_policy_version TEXT NOT NULL,
    evidence_strength_policy_json TEXT NOT NULL,
    calculation_quantum TEXT NOT NULL,
    display_quantum TEXT NOT NULL,
    rounding_mode TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('available','revoked')),
    CHECK (
        (scope = 'global' AND scope_value IS NULL) OR
        (scope = 'symbol' AND scope_value IS NOT NULL)
    ),
    UNIQUE(logical_profile_id, revision_number)
);

CREATE INDEX idx_synthesis_profile_asof
ON synthesis_profile_revisions(
    scope, scope_value, available_at, ingested_at,
    logical_profile_id, revision_number
);

CREATE TABLE synthesis_profile_approvals (
    approval_event_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    profile_revision_id TEXT NOT NULL REFERENCES synthesis_profile_revisions(id),
    decision TEXT NOT NULL CHECK (decision IN ('approved','revoked')),
    rule_id TEXT NOT NULL CHECK (rule_id = 'TGT-01'),
    rule_version TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK (evidence_level = 'C'),
    implementation_mode TEXT NOT NULL CHECK (implementation_mode = 'project_operationalization'),
    project_operationalization INTEGER NOT NULL CHECK (project_operationalization = 1),
    approved_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX idx_synthesis_profile_approval_asof
ON synthesis_profile_approvals(profile_revision_id, rule_id, approved_at, ingested_at);

CREATE TABLE synthesis_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('profile_revision','profile_approval')),
    resource_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);

CREATE TABLE analysis_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    knowledge_cutoff_at TEXT NOT NULL,
    capture_mode TEXT NOT NULL CHECK (capture_mode IN ('live_refresh','historical_reconstruction')),
    model_version TEXT NOT NULL,
    synthesis_profile_revision_id TEXT REFERENCES synthesis_profile_revisions(id),
    synthesis_profile_approval_id TEXT REFERENCES synthesis_profile_approvals(approval_id),
    used_rule_versions_json TEXT NOT NULL,
    source_resource_versions_json TEXT NOT NULL,
    manual_approval_ids_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    supersedes_snapshot_id TEXT REFERENCES analysis_snapshots(snapshot_id),
    CHECK (
        (synthesis_profile_revision_id IS NULL AND synthesis_profile_approval_id IS NULL) OR
        (synthesis_profile_revision_id IS NOT NULL AND synthesis_profile_approval_id IS NOT NULL)
    )
);

CREATE INDEX idx_analysis_snapshots_symbol
ON analysis_snapshots(symbol, knowledge_cutoff_at, created_at);

CREATE TABLE analysis_snapshot_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES analysis_snapshots(snapshot_id),
    bound_at TEXT NOT NULL
);

CREATE TRIGGER analysis_snapshots_no_update
BEFORE UPDATE ON analysis_snapshots
BEGIN
    SELECT RAISE(ABORT, 'analysis_snapshots are immutable');
END;

CREATE TRIGGER analysis_snapshots_no_delete
BEFORE DELETE ON analysis_snapshots
BEGIN
    SELECT RAISE(ABORT, 'analysis_snapshots are immutable');
END;
