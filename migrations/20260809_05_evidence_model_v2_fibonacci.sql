CREATE TABLE technical_anchor_revisions (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_anchor_set_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    revision_of TEXT,
    symbol TEXT NOT NULL,
    evidence_basis_rule_id TEXT NOT NULL CHECK (evidence_basis_rule_id IN ('FB-03','FB-04')),
    anchors_json TEXT NOT NULL,
    price_unit TEXT NOT NULL CHECK (price_unit = 'TWD_per_share'),
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    source TEXT NOT NULL,
    source_note TEXT,
    status TEXT NOT NULL CHECK (status IN ('available','revoked')),
    UNIQUE(logical_anchor_set_id, revision_number)
);

CREATE INDEX idx_technical_anchor_asof
ON technical_anchor_revisions(symbol, logical_anchor_set_id, available_at, ingested_at, revision_number);

CREATE TABLE technical_anchor_approvals (
    approval_event_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    anchor_revision_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved','revoked')),
    rule_id TEXT NOT NULL CHECK (rule_id IN ('FB-03','FB-04')),
    rule_version TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK (evidence_level = 'A'),
    implementation_mode TEXT NOT NULL CHECK (implementation_mode = 'verified_core'),
    project_operationalization INTEGER NOT NULL CHECK (project_operationalization = 0),
    approved_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    FOREIGN KEY(anchor_revision_id) REFERENCES technical_anchor_revisions(id)
);

CREATE INDEX idx_technical_anchor_approval_asof
ON technical_anchor_approvals(anchor_revision_id, rule_id, approved_at, ingested_at);

CREATE TABLE technical_anchor_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('anchor_revision','anchor_approval')),
    resource_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);
