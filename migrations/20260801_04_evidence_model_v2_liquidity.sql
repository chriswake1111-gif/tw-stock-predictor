CREATE TABLE cbc_m1b_monthly (
    id TEXT PRIMARY KEY,
    period TEXT NOT NULL,
    value_raw REAL NOT NULL,
    raw_unit TEXT NOT NULL,
    value_twd REAL NOT NULL,
    data_date TEXT NOT NULL,
    available_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_dataset TEXT NOT NULL,
    source_url TEXT,
    payload_hash TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL CHECK (status IN ('available', 'revoked')),
    quality_note TEXT,
    UNIQUE(period, revision, source_dataset)
);

CREATE INDEX idx_cbc_m1b_monthly_asof
ON cbc_m1b_monthly(available_at, ingested_at, period, revision);

CREATE TABLE market_turnover_daily (
    id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    twse_turnover_twd REAL,
    tpex_turnover_twd REAL,
    total_turnover_twd REAL,
    twse_source TEXT,
    tpex_source TEXT,
    twse_dataset TEXT,
    tpex_dataset TEXT,
    twse_payload_hash TEXT,
    tpex_payload_hash TEXT,
    available_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL CHECK (status IN ('available', 'partial', 'revoked')),
    quality_note TEXT,
    UNIQUE(trade_date, revision)
);

CREATE INDEX idx_market_turnover_daily_asof
ON market_turnover_daily(trade_date, available_at, ingested_at, revision);
