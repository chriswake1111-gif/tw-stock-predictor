CREATE TABLE deployment_plan_revisions (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_campaign_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    revision_of TEXT,
    symbol TEXT NOT NULL,
    planned_total_capital TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'TWD'),
    triggers_json TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    source_note TEXT,
    status TEXT NOT NULL CHECK (status IN ('available','revoked')),
    UNIQUE(logical_campaign_id, revision_number)
);

CREATE INDEX idx_deployment_plan_asof
ON deployment_plan_revisions(symbol, logical_campaign_id, available_at, ingested_at, revision_number);

CREATE TABLE deployment_plan_approvals (
    approval_event_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    plan_revision_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved','revoked')),
    rule_id TEXT NOT NULL CHECK (rule_id = 'ENT-02'),
    rule_version TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK (evidence_level = 'A'),
    implementation_mode TEXT NOT NULL CHECK (implementation_mode = 'verified_core'),
    project_operationalization INTEGER NOT NULL CHECK (project_operationalization = 0),
    approved_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    FOREIGN KEY(plan_revision_id) REFERENCES deployment_plan_revisions(id)
);

CREATE INDEX idx_deployment_approval_asof
ON deployment_plan_approvals(plan_revision_id, rule_id, approved_at, ingested_at);

CREATE TABLE deployment_plan_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('plan_revision','plan_approval')),
    resource_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);
