CREATE TABLE forward_eps_observations (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_series_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    revision_of TEXT NULL REFERENCES forward_eps_observations(id),
    symbol TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    eps_low REAL NULL,
    eps_base REAL NOT NULL,
    eps_high REAL NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('broker_report','company_guidance','consensus_api','manual')),
    published_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    analyst_count INTEGER NULL,
    quality_note TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','revoked')),
    unit TEXT NOT NULL,
    UNIQUE (logical_series_id, revision_number)
);

CREATE INDEX idx_forward_eps_asof
ON forward_eps_observations(symbol, available_at, ingested_at, logical_series_id, revision_number);

CREATE TABLE pe_scenarios (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    logical_series_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    revision_of TEXT NULL REFERENCES pe_scenarios(id),
    label TEXT NOT NULL,
    pe_value REAL NOT NULL CHECK (pe_value > 0),
    rationale TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK (evidence_level IN ('A','B','C','U')),
    scope TEXT NOT NULL CHECK (scope IN ('symbol','industry','market')),
    symbol TEXT NULL,
    industry TEXT NULL,
    market TEXT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    approval_status TEXT NOT NULL CHECK (approval_status IN ('draft','approved','revoked')),
    approved_by TEXT NULL,
    approved_at TEXT NULL,
    effective_from TEXT NULL,
    effective_to TEXT NULL,
    version TEXT NOT NULL,
    CHECK (
        (scope = 'symbol' AND symbol IS NOT NULL AND industry IS NULL AND market IS NULL) OR
        (scope = 'industry' AND symbol IS NULL AND industry IS NOT NULL AND market IS NULL) OR
        (scope = 'market' AND symbol IS NULL AND industry IS NULL AND market IS NOT NULL)
    ),
    UNIQUE (logical_series_id, revision_number)
);

CREATE INDEX idx_pe_scenarios_asof
ON pe_scenarios(scope, symbol, industry, market, available_at, ingested_at, logical_series_id, revision_number);
