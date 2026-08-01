CREATE TABLE valuation_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('forward_eps','pe_scenario','approval')),
    resource_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);

CREATE INDEX idx_valuation_idempotency_resource
ON valuation_idempotency_keys(resource_type, resource_id);

INSERT OR IGNORE INTO valuation_idempotency_keys (
    idempotency_key, payload_fingerprint, resource_type, resource_id, bound_at
)
SELECT idempotency_key, payload_fingerprint, 'forward_eps', id, ingested_at
FROM forward_eps_observations;

INSERT OR IGNORE INTO valuation_idempotency_keys (
    idempotency_key, payload_fingerprint, resource_type, resource_id, bound_at
)
SELECT idempotency_key, payload_fingerprint, 'pe_scenario', id, ingested_at
FROM pe_scenarios;

CREATE TABLE valuation_approvals (
    approval_event_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('forward_eps','pe_scenario')),
    resource_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved','revoked')),
    rule_id TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK (evidence_level IN ('A','B','C','U')),
    project_operationalization INTEGER NOT NULL CHECK (project_operationalization IN (0,1)),
    approved_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX idx_valuation_approvals_asof
ON valuation_approvals(resource_type, resource_id, available_at, ingested_at);
