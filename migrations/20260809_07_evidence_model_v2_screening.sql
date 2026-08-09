CREATE TABLE security_valuation_observations (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_observation_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    revision_of TEXT REFERENCES security_valuation_observations(id),
    symbol TEXT NOT NULL,
    metric_date TEXT NOT NULL,
    pe REAL,
    pb REAL,
    dividend_yield_ratio REAL,
    source_name TEXT NOT NULL,
    source_dataset TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('available','revoked')),
    dividend_yield_unit TEXT NOT NULL CHECK (dividend_yield_unit = 'ratio'),
    UNIQUE(logical_observation_id, revision_number)
);

CREATE INDEX idx_security_valuation_asof
ON security_valuation_observations(
    symbol, source_name, source_dataset, metric_date,
    available_at, ingested_at, logical_observation_id, revision_number
);

CREATE TABLE screening_profile_revisions (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_profile_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    revision_of TEXT REFERENCES screening_profile_revisions(id),
    scope TEXT NOT NULL CHECK (scope IN ('global','symbol','industry')),
    scope_value TEXT,
    valuation_basis TEXT NOT NULL CHECK (valuation_basis IN ('self_history','industry')),
    valuation_source_name TEXT NOT NULL,
    valuation_source_dataset TEXT NOT NULL,
    pe_percentile_max REAL NOT NULL CHECK (pe_percentile_max >= 0 AND pe_percentile_max <= 100),
    pb_percentile_max REAL NOT NULL CHECK (pb_percentile_max >= 0 AND pb_percentile_max <= 100),
    dividend_yield_percentile_min REAL NOT NULL CHECK (
        dividend_yield_percentile_min >= 0 AND dividend_yield_percentile_min <= 100
    ),
    history_years INTEGER NOT NULL CHECK (history_years >= 1),
    minimum_observations INTEGER NOT NULL CHECK (minimum_observations >= 20),
    forward_eps_growth_required INTEGER NOT NULL CHECK (forward_eps_growth_required = 1),
    forward_eps_source_name TEXT NOT NULL,
    forward_eps_source_type TEXT NOT NULL CHECK (
        forward_eps_source_type IN ('broker_report','company_guidance','consensus_api','manual')
    ),
    forward_eps_growth_convention TEXT NOT NULL CHECK (
        forward_eps_growth_convention = 'consecutive_fiscal_year_base'
    ),
    technical_component TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('available','revoked')),
    CHECK (
        (scope = 'global' AND scope_value IS NULL) OR
        (scope IN ('symbol','industry') AND scope_value IS NOT NULL)
    ),
    UNIQUE(logical_profile_id, revision_number)
);

CREATE INDEX idx_screening_profile_asof
ON screening_profile_revisions(
    scope, scope_value, available_at, ingested_at, logical_profile_id, revision_number
);

CREATE TABLE screening_profile_approvals (
    approval_event_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    profile_revision_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved','revoked')),
    rule_id TEXT NOT NULL CHECK (rule_id = 'SEL-01'),
    rule_version TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK (evidence_level = 'C'),
    implementation_mode TEXT NOT NULL CHECK (implementation_mode = 'project_operationalization'),
    project_operationalization INTEGER NOT NULL CHECK (project_operationalization = 1),
    approved_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    FOREIGN KEY(profile_revision_id) REFERENCES screening_profile_revisions(id)
);

CREATE INDEX idx_screening_profile_approval_asof
ON screening_profile_approvals(profile_revision_id, rule_id, approved_at, ingested_at);

CREATE TABLE screening_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (
        resource_type IN ('valuation_observation','profile_revision','profile_approval')
    ),
    resource_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);

CREATE INDEX idx_screening_idempotency_resource
ON screening_idempotency_keys(resource_type, resource_id);
