/* Phase 14 official end-of-day close context. Additive and transactional. */

/* The Phase 10 resource registry predates the two approved EOD resource
   kinds. Rebuild only this registry table so its existing rows and child
   foreign keys remain intact while the allowed kinds become explicit. */
CREATE TABLE data_resources_phase14 (
    resource_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES data_providers(provider_id),
    logical_resource_key TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (
        resource_type IN (
            'market_turnover','monetary_statistic','trading_calendar',
            'symbol_master','corporate_action','eod_close',
            'product_classification'
        )
    ),
    market TEXT NOT NULL,
    expected_frequency TEXT NOT NULL CHECK (
        expected_frequency IN ('daily','monthly_publication','periodic','manual')
    ),
    freshness_policy TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    storage_policy TEXT NOT NULL CHECK (
        storage_policy IN ('archive_raw','archive_normalized','hash_only')
    ),
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    UNIQUE(provider_id, logical_resource_key)
);

INSERT INTO data_resources_phase14 (
    resource_id, provider_id, logical_resource_key, resource_type, market,
    expected_frequency, freshness_policy, parser_id, parser_version,
    schema_version, storage_policy, enabled, created_at, payload_fingerprint
)
SELECT resource_id, provider_id, logical_resource_key, resource_type, market,
       expected_frequency, freshness_policy, parser_id, parser_version,
       schema_version, storage_policy, enabled, created_at, payload_fingerprint
FROM data_resources;

DROP TABLE data_resources;
ALTER TABLE data_resources_phase14 RENAME TO data_resources;

INSERT INTO data_providers (
    provider_id, display_name, authority_tier, provider_type, base_identity,
    enabled, created_at, payload_fingerprint
)
VALUES (
    'twse-isin-official', 'TWSE ISIN Official', 'authoritative', 'official',
    'https://isin.twse.com.tw', 1, '2026-08-27T00:00:00Z',
    'c764668cbb0d7a03ee52ca92bf4e09fc13260ad460be425ccd427f4dabfc48fe'
)
ON CONFLICT(provider_id) DO NOTHING;

/* The Phase 13 official provider identities are deliberately reused for the
   two price resources. The ISIN classifier has its own exact provider and
   resource identity. */
INSERT INTO data_resources (
    resource_id, provider_id, logical_resource_key, resource_type, market,
    expected_frequency, freshness_policy, parser_id, parser_version,
    schema_version, storage_policy, enabled, created_at, payload_fingerprint
)
VALUES
(
    'twse.eod.stock_day_all', 'twse-universe-official',
    'twse.eod.stock_day_all', 'eod_close', 'TWSE', 'daily',
    'eod_currentness_policy_v1', 'twse_eod_stock_day_all', '1',
    'eod_close_v1', 'archive_raw', 1, '2026-08-27T00:00:00Z',
    '164d60344ed21ceea58560744f4fbc7436b7dc1afede53b628afe58928c4171a'
),
(
    'tpex.eod.daily_close_quotes', 'tpex-universe-official',
    'tpex.eod.daily_close_quotes', 'eod_close', 'TPEX', 'daily',
    'eod_currentness_policy_v1', 'tpex_eod_daily_close_quotes', '1',
    'eod_close_v1', 'archive_raw', 1, '2026-08-27T00:00:00Z',
    'e4acbd637e638eb7a78ec2fc487555689ffab58b7e53d09c85dae06fb59379c9'
),
(
    'twse.isin.security_classification', 'twse-isin-official',
    'twse.isin.security_classification', 'product_classification',
    'TWSE_ISIN', 'manual', 'eod_product_scope_v1',
    'twse_isin_security_classification', '1', 'eod_product_scope_v1',
    'archive_raw', 1, '2026-08-27T00:00:00Z',
    '74df7f85e702f72da6bd6428d57ceb01fe9a8e0d43cf4e5a1c3dd18df9a30b92'
)
ON CONFLICT(resource_id) DO NOTHING;

CREATE TABLE eod_price_resource_policies (
    resource_id TEXT PRIMARY KEY REFERENCES data_resources(resource_id),
    source_trading_scope TEXT NOT NULL,
    price_semantics_version TEXT NOT NULL CHECK (
        price_semantics_version = 'official_reported_close_v1'
    ),
    currency_unit_policy_version TEXT NOT NULL CHECK (
        currency_unit_policy_version = 'eod_currency_unit_scope_v1'
    ),
    currency TEXT NOT NULL CHECK (currency = 'TWD'),
    unit TEXT NOT NULL CHECK (unit = 'TWD_per_share'),
    approved_unit_evidence_reference TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    write_enabled INTEGER NOT NULL CHECK (write_enabled = 0),
    created_at TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL UNIQUE
);

INSERT INTO eod_price_resource_policies (
    resource_id, source_trading_scope, price_semantics_version,
    currency_unit_policy_version, currency, unit,
    approved_unit_evidence_reference, enabled, write_enabled, created_at,
    policy_fingerprint
)
VALUES
(
    'twse.eod.stock_day_all', 'twse_whole_market_daily_close',
    'official_reported_close_v1', 'eod_currency_unit_scope_v1', 'TWD',
    'TWD_per_share',
    'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL#ClosingPrice',
    1, 0, '2026-08-27T00:00:00Z',
    '09a80ff6633ed265b972573abfada5c9c3261404c0ce6144f3aee3a043e8c126'
),
(
    'tpex.eod.daily_close_quotes',
    'tpex_mainboard_daily_close_quotes_without_fixed_price',
    'official_reported_close_v1', 'eod_currency_unit_scope_v1', 'TWD',
    'TWD_per_share',
    'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes#Close',
    1, 0, '2026-08-27T00:00:00Z',
    'a336016523ca1039506053d296f1d99d97b41cfbee7112436722d5117742d1eb'
)
ON CONFLICT(resource_id) DO NOTHING;

CREATE TABLE eod_close_source_snapshots (
    source_snapshot_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    raw_resource_revision_id TEXT NOT NULL REFERENCES raw_resource_revisions(raw_resource_revision_id),
    logical_revision_key TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    source_trade_date TEXT,
    source_trade_date_status TEXT NOT NULL CHECK (
        source_trade_date_status IN ('valid','missing','invalid')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('available','partial','unknown','insufficient_data',
                   'provider_error','schema_changed','blocked','revoked','rejected')
    ),
    coverage_state TEXT NOT NULL CHECK (coverage_state IN ('complete','partial','unknown')),
    coverage_proof_type TEXT,
    coverage_proof_reference TEXT,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    source_date_min TEXT,
    source_date_max TEXT,
    source_published_at TEXT,
    fetched_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    available_at TEXT,
    ingested_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    http_method TEXT NOT NULL CHECK (http_method = 'GET'),
    response_format TEXT NOT NULL CHECK (response_format = 'json'),
    contract_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    normalized_payload_sha256 TEXT NOT NULL,
    query_dimensions_json TEXT NOT NULL,
    source_record_reference TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    reason TEXT,
    supersedes_source_snapshot_id TEXT REFERENCES eod_close_source_snapshots(source_snapshot_id),
    revocation_reference TEXT,
    identity_fingerprint TEXT NOT NULL UNIQUE,
    UNIQUE(resource_id, logical_revision_key, raw_payload_sha256),
    CHECK (
        (coverage_state = 'partial' AND coverage_proof_type IS NOT NULL
         AND coverage_proof_reference IS NOT NULL)
        OR coverage_state <> 'partial'
    ),
    CHECK (available_at IS NULL OR available_at <= received_at)
);

CREATE INDEX idx_eod_source_snapshot_trade_date
ON eod_close_source_snapshots(resource_id, source_trade_date, available_at,
                               ingested_at, revision_number, source_snapshot_id);
CREATE INDEX idx_eod_source_snapshot_current
ON eod_close_source_snapshots(resource_id, ingested_at, available_at,
                               revision_number, source_snapshot_id);

CREATE TABLE eod_product_classification_evidence (
    classification_evidence_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    raw_resource_revision_id TEXT NOT NULL REFERENCES raw_resource_revisions(raw_resource_revision_id),
    logical_revision_key TEXT NOT NULL,
    official_code TEXT NOT NULL CHECK (length(trim(official_code)) > 0),
    venue_raw TEXT,
    market_raw TEXT,
    security_type_raw TEXT,
    isin_raw TEXT,
    listing_date TEXT,
    cfi_raw TEXT,
    currency_raw TEXT,
    remarks_raw TEXT,
    raw_cells_json TEXT NOT NULL,
    classification_decision TEXT NOT NULL CHECK (
        classification_decision IN ('supported_stock','not_applicable','needs_human_input')
    ),
    classification_state TEXT NOT NULL CHECK (
        classification_state IN ('accepted','blocked','schema_changed','ambiguous','revoked','unknown')
    ),
    reason TEXT,
    contract_version TEXT NOT NULL CHECK (contract_version = 'eod_product_scope_v1'),
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    supersedes_classification_evidence_id TEXT REFERENCES eod_product_classification_evidence(classification_evidence_id),
    revocation_reference TEXT,
    source_published_at TEXT,
    fetched_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    available_at TEXT,
    ingested_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    normalized_payload_sha256 TEXT NOT NULL,
    source_record_reference TEXT NOT NULL,
    identity_fingerprint TEXT NOT NULL UNIQUE,
    UNIQUE(resource_id, official_code, raw_payload_sha256),
    CHECK (available_at IS NULL OR available_at <= received_at)
);

CREATE INDEX idx_eod_classification_lookup
ON eod_product_classification_evidence(resource_id, official_code, available_at,
                                        ingested_at, revision_number,
                                        classification_evidence_id);

CREATE TABLE eod_close_observations (
    close_observation_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    raw_resource_revision_id TEXT NOT NULL REFERENCES raw_resource_revisions(raw_resource_revision_id),
    source_snapshot_id TEXT NOT NULL REFERENCES eod_close_source_snapshots(source_snapshot_id),
    classification_evidence_id TEXT REFERENCES eod_product_classification_evidence(classification_evidence_id),
    instrument_id TEXT REFERENCES universe_instruments(instrument_id),
    instrument_revision_id TEXT REFERENCES universe_instrument_revisions(instrument_revision_id),
    venue TEXT NOT NULL CHECK (venue IN ('TWSE','TPEX')),
    official_code TEXT NOT NULL CHECK (length(trim(official_code)) > 0),
    trade_date TEXT,
    trade_date_status TEXT NOT NULL CHECK (trade_date_status IN ('valid','missing','invalid')),
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    supersedes_observation_id TEXT REFERENCES eod_close_observations(close_observation_id),
    raw_close_text TEXT,
    close_value TEXT,
    raw_volume_text TEXT,
    volume_value TEXT,
    raw_trade_indication_text TEXT,
    trade_indication_value TEXT,
    currency TEXT,
    unit TEXT,
    price_semantics_version TEXT,
    product_scope TEXT NOT NULL CHECK (
        product_scope IN ('supported_stock','not_applicable','needs_human_input')
    ),
    observation_status TEXT NOT NULL CHECK (
        observation_status IN ('available','insufficient_data','partial','unknown',
                               'blocked','needs_human_input','not_applicable')
    ),
    public_eligibility_status TEXT NOT NULL CHECK (
        public_eligibility_status IN ('eligible','ineligible','awaiting_review')
    ),
    quality_status TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL,
    row_fingerprint TEXT NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    normalized_payload_sha256 TEXT NOT NULL,
    source_trading_scope TEXT NOT NULL,
    available_at TEXT,
    ingested_at TEXT NOT NULL,
    source_record_reference TEXT NOT NULL,
    source_note TEXT,
    identity_fingerprint TEXT NOT NULL UNIQUE,
    UNIQUE(resource_id, official_code, trade_date, revision_number),
    CHECK (available_at IS NULL OR available_at <= ingested_at),
    CHECK (public_eligibility_status <> 'eligible' OR close_value IS NOT NULL)
);

CREATE INDEX idx_eod_observation_date
ON eod_close_observations(resource_id, trade_date, available_at, ingested_at,
                           revision_number, close_observation_id);
CREATE INDEX idx_eod_observation_identity
ON eod_close_observations(instrument_revision_id, trade_date, available_at,
                          ingested_at);
CREATE INDEX idx_eod_observation_code
ON eod_close_observations(venue, official_code, trade_date, available_at,
                          ingested_at);

CREATE TABLE eod_ingestion_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    raw_resource_revision_id TEXT NOT NULL REFERENCES raw_resource_revisions(raw_resource_revision_id),
    source_snapshot_id TEXT REFERENCES eod_close_source_snapshots(source_snapshot_id),
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_eod_ingestion_idempotency_resource
ON eod_ingestion_idempotency(resource_id, payload_fingerprint);

CREATE TRIGGER eod_price_resource_policies_no_update
BEFORE UPDATE ON eod_price_resource_policies
BEGIN
    SELECT RAISE(ABORT, 'eod price resource policies are immutable');
END;
CREATE TRIGGER eod_price_resource_policies_no_delete
BEFORE DELETE ON eod_price_resource_policies
BEGIN
    SELECT RAISE(ABORT, 'eod price resource policies are immutable');
END;

CREATE TRIGGER eod_close_source_snapshots_no_update
BEFORE UPDATE ON eod_close_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'eod source snapshots are immutable');
END;
CREATE TRIGGER eod_close_source_snapshots_no_delete
BEFORE DELETE ON eod_close_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'eod source snapshots are immutable');
END;

CREATE TRIGGER eod_product_classification_evidence_no_update
BEFORE UPDATE ON eod_product_classification_evidence
BEGIN
    SELECT RAISE(ABORT, 'eod classification evidence is immutable');
END;
CREATE TRIGGER eod_product_classification_evidence_no_delete
BEFORE DELETE ON eod_product_classification_evidence
BEGIN
    SELECT RAISE(ABORT, 'eod classification evidence is immutable');
END;

CREATE TRIGGER eod_close_observations_no_update
BEFORE UPDATE ON eod_close_observations
BEGIN
    SELECT RAISE(ABORT, 'eod close observations are immutable');
END;
CREATE TRIGGER eod_close_observations_no_delete
BEFORE DELETE ON eod_close_observations
BEGIN
    SELECT RAISE(ABORT, 'eod close observations are immutable');
END;

CREATE TRIGGER eod_ingestion_idempotency_no_update
BEFORE UPDATE ON eod_ingestion_idempotency
BEGIN
    SELECT RAISE(ABORT, 'eod ingestion idempotency is immutable');
END;
CREATE TRIGGER eod_ingestion_idempotency_no_delete
BEFORE DELETE ON eod_ingestion_idempotency
BEGIN
    SELECT RAISE(ABORT, 'eod ingestion idempotency is immutable');
END;
