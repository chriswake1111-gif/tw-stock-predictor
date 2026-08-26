/* Phase 13 Universe Foundation. Additive only. */

CREATE TABLE universe_resource_policies (
    resource_id TEXT PRIMARY KEY REFERENCES data_resources(resource_id),
    resource_role TEXT NOT NULL CHECK (resource_role IN (
        'master_snapshot','listing_lifecycle_event','trading_operational_event',
        'corroborating_identity_observation','licensed_reference_file'
    )),
    availability_mode TEXT NOT NULL CHECK (availability_mode IN (
        'official_timestamp','conservative_first_observed',
        'manual_publication_evidence_required'
    )),
    freshness_mode TEXT NOT NULL CHECK (freshness_mode IN (
        'unknown_without_official_cadence','event_observation',
        'official_cadence_window','licensed_reference'
    )),
    completeness_policy TEXT NOT NULL,
    mapping_policy_version TEXT NOT NULL CHECK (mapping_policy_version = 'universe_symbol_mapping_v1'),
    source_scope TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL UNIQUE
);

CREATE TABLE universe_instruments (
    instrument_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL CHECK (venue IN ('TWSE','TPEX')),
    official_code TEXT NOT NULL CHECK (length(trim(official_code)) > 0),
    identity_epoch INTEGER NOT NULL CHECK (identity_epoch >= 1),
    identity_binding_fingerprint TEXT NOT NULL UNIQUE,
    first_observed_at TEXT NOT NULL,
    first_source_reference TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    display_name TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (venue, official_code, identity_epoch)
);

CREATE INDEX idx_universe_instruments_code
ON universe_instruments(venue, official_code, identity_epoch);

CREATE TABLE universe_revisions (
    universe_revision_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    raw_resource_revision_id TEXT REFERENCES raw_resource_revisions(raw_resource_revision_id),
    logical_revision_key TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    source_published_at TEXT,
    source_effective_date TEXT,
    fetched_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    first_observed_at TEXT,
    available_at TEXT,
    ingested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'accepted','partial','provider_error','schema_changed','awaiting_review','revoked','rejected'
    )),
    reason TEXT,
    payload_sha256 TEXT,
    schema_fingerprint TEXT,
    parser_version TEXT,
    source_reference TEXT,
    publication_evidence_id TEXT REFERENCES resource_publication_evidence(publication_evidence_id),
    supersedes_revision_id TEXT REFERENCES universe_revisions(universe_revision_id),
    UNIQUE(resource_id, logical_revision_key, revision_number),
    CHECK (status IN ('accepted','partial') OR length(trim(COALESCE(reason,''))) > 0),
    CHECK (available_at IS NULL OR available_at <= ingested_at)
);

CREATE INDEX idx_universe_revisions_asof
ON universe_revisions(resource_id, logical_revision_key, available_at, ingested_at, revision_number);

CREATE TABLE universe_instrument_revisions (
    instrument_revision_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES universe_instruments(instrument_id),
    universe_revision_id TEXT NOT NULL REFERENCES universe_revisions(universe_revision_id),
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    venue TEXT NOT NULL CHECK (venue IN ('TWSE','TPEX')),
    official_code TEXT NOT NULL CHECK (length(trim(official_code)) > 0),
    canonical_symbol TEXT,
    mapping_basis TEXT,
    security_type TEXT NOT NULL DEFAULT 'unknown',
    display_name TEXT,
    listing_status TEXT NOT NULL CHECK (listing_status IN ('listed','delisted','unknown')),
    trading_state TEXT NOT NULL CHECK (trading_state IN ('normal','suspended','altered','periodic','managed','unknown')),
    membership_state TEXT NOT NULL,
    source_effective_date TEXT,
    source_effective_at TEXT,
    source_published_at TEXT,
    first_observed_at TEXT,
    received_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    available_at TEXT,
    ingested_at TEXT NOT NULL,
    availability_mode TEXT NOT NULL CHECK (availability_mode IN (
        'official_timestamp','conservative_first_observed','manual_publication_evidence_required'
    )),
    freshness_mode TEXT NOT NULL CHECK (freshness_mode IN (
        'unknown_without_official_cadence','event_observation','official_cadence_window','licensed_reference'
    )),
    freshness_status TEXT NOT NULL CHECK (freshness_status IN ('current','stale','unknown','blocked')),
    current_complete INTEGER NOT NULL CHECK (current_complete IN (0,1)),
    coverage_complete INTEGER NOT NULL CHECK (coverage_complete IN (0,1)),
    status TEXT NOT NULL CHECK (status IN (
        'accepted','partial','provider_error','schema_changed','awaiting_review','revoked','rejected'
    )),
    reason TEXT,
    source_reference TEXT,
    payload_sha256 TEXT,
    schema_fingerprint TEXT,
    parser_version TEXT,
    effective_from TEXT,
    effective_to TEXT,
    supersedes_revision_id TEXT REFERENCES universe_instrument_revisions(instrument_revision_id),
    UNIQUE(instrument_id, revision_number),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_from < effective_to),
    CHECK (status IN ('accepted','partial') OR length(trim(COALESCE(reason,''))) > 0)
);

CREATE INDEX idx_universe_instrument_revision_asof
ON universe_instrument_revisions(instrument_id, available_at, ingested_at, revision_number);
CREATE INDEX idx_universe_instrument_revision_canonical
ON universe_instrument_revisions(canonical_symbol, available_at, ingested_at);

CREATE TABLE universe_identity_alias_events (
    alias_event_id TEXT PRIMARY KEY,
    from_instrument_id TEXT NOT NULL REFERENCES universe_instruments(instrument_id),
    to_instrument_id TEXT NOT NULL REFERENCES universe_instruments(instrument_id),
    alias_code TEXT NOT NULL,
    alias_venue TEXT NOT NULL CHECK (alias_venue IN ('TWSE','TPEX')),
    alias_type TEXT NOT NULL CHECK (alias_type IN ('successor','previous_code','official_alias')),
    effective_at TEXT,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    UNIQUE(from_instrument_id, to_instrument_id, alias_code, alias_type)
);

CREATE TABLE universe_lifecycle_events (
    lifecycle_event_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES universe_instruments(instrument_id),
    event_type TEXT NOT NULL CHECK (event_type IN ('listed','terminated','resumed','successor')),
    event_date TEXT,
    effective_at TEXT,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted','revoked','awaiting_review')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0)
);

CREATE TABLE universe_operational_state_events (
    operational_event_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES universe_instruments(instrument_id),
    trading_state TEXT NOT NULL CHECK (trading_state IN ('normal','suspended','altered','periodic','managed','unknown')),
    effective_at TEXT,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted','revoked','awaiting_review')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0)
);

CREATE TABLE universe_ingestion_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    payload_fingerprint TEXT NOT NULL,
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    universe_revision_id TEXT NOT NULL REFERENCES universe_revisions(universe_revision_id),
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(idempotency_key)
);

CREATE INDEX idx_universe_idempotency_resource
ON universe_ingestion_idempotency(resource_id, payload_fingerprint);

/* Approved registry seed contains only identity/lifecycle/operational roles. */
INSERT INTO data_providers
(provider_id, display_name, authority_tier, provider_type, base_identity, enabled, created_at, payload_fingerprint)
VALUES
('twse-universe-official','TWSE Universe Official','authoritative','official','https://www.twse.com.tw',1,'2026-08-21T00:00:00Z',
 '042ecef36a6ca8dd109b948f6a679973b31e69c759d7e9d1e408b1f83746cb3e'),
('tpex-universe-official','TPEx Universe Official','authoritative','official','https://www.tpex.org.tw',1,'2026-08-21T00:00:00Z',
 'add538b4692192633b1142339113d9a5009e68ab309c83d5de69f0657e7eb3bb')
ON CONFLICT(provider_id) DO NOTHING;

INSERT INTO data_resources
(resource_id, provider_id, logical_resource_key, resource_type, market, expected_frequency, freshness_policy,
 parser_id, parser_version, schema_version, storage_policy, enabled, created_at, payload_fingerprint)
VALUES
('twse-universe-master','twse-universe-official','twse.t187ap03_L','symbol_master','TWSE','periodic','unknown_without_official_cadence','twse_universe_master','1','phase13','archive_raw',1,'2026-08-21T00:00:00Z','be3665c8f7640f7031de4c8526eeace37873f34df8adebdff32792f94e8e808d'),
('twse-universe-newlisting','twse-universe-official','twse.company.newlisting','corporate_action','TWSE','periodic','event_observation','twse_universe_newlisting','1','phase13','archive_raw',1,'2026-08-21T00:00:00Z','7a521d559be9f20658c194b2b8bc5e11584b3b883c0550dd25148f9a2bf0bbbd'),
('twse-universe-termination','twse-universe-official','twse.company.suspendListingCsvAndHtml','corporate_action','TWSE','periodic','manual_publication_evidence_required','twse_universe_termination','1','phase13','archive_raw',1,'2026-08-21T00:00:00Z','c9f0bb3f8ec9c7bfdd40bb56a21bafc6d7d947e0e4b2cc4be0b9edb5b33e8502'),
('tpex-universe-master','tpex-universe-official','tpex.mopsfin_t187ap03_O','symbol_master','TPEX','periodic','unknown_without_official_cadence','tpex_universe_master','1','phase13','archive_raw',1,'2026-08-21T00:00:00Z','afeb2b0cd9e896bc8a3d0e3b77410557a0d3357ea3bdd94c7ede4e1274265d66'),
('tpex-universe-lifecycle','tpex-universe-official','tpex.company.deListed','corporate_action','TPEX','manual','manual_publication_evidence_required','tpex_universe_lifecycle','1','phase13','archive_raw',1,'2026-08-21T00:00:00Z','355e21480d1413692b2698e4fe82cd16c87b5de553b76ca0504f92378d738cb7'),
('tpex-universe-operational','tpex-universe-official','tpex.spendi.cmode','trading_calendar','TPEX','periodic','event_observation','tpex_universe_operational','1','phase13','archive_raw',1,'2026-08-21T00:00:00Z','53f96df666aa8c19fdf4b3e0254d9e1033ff617a9273c7789345e65a430d5884'),
('tpex-universe-corroborating','tpex-universe-official','tpex.company.current','symbol_master','TPEX','manual','manual_publication_evidence_required','tpex_universe_company','1','phase13','archive_raw',1,'2026-08-21T00:00:00Z','aadfe1261421f17cb48341c4721d09575c7ac667b5c5a65b1645a14cf6b2f254')
ON CONFLICT(resource_id) DO NOTHING;

INSERT INTO universe_resource_policies
(resource_id,resource_role,availability_mode,freshness_mode,completeness_policy,mapping_policy_version,source_scope,enabled,created_at,policy_fingerprint)
VALUES
('twse-universe-master','master_snapshot','conservative_first_observed','unknown_without_official_cadence','accepted_master_complete','universe_symbol_mapping_v1','TWSE t187ap03_L basic master',1,'2026-08-21T00:00:00Z','93588835b58c11ee96ec28266ae2820de103a9a78fdc8bb8572448f98b8cf9fd'),
('twse-universe-newlisting','listing_lifecycle_event','conservative_first_observed','event_observation','explicit_listing_event','universe_symbol_mapping_v1','TWSE company/newlisting',1,'2026-08-21T00:00:00Z','6670de4e7d08da5ec3cf25aee65c9791f90f4882c7589632e40b09b51db351c7'),
('twse-universe-termination','listing_lifecycle_event','manual_publication_evidence_required','event_observation','explicit_termination_only','universe_symbol_mapping_v1','TWSE company/suspendListingCsvAndHtml termination candidate',1,'2026-08-21T00:00:00Z','c9f0bb3f8ec9c7bfdd40bb56a21bafc6d7d947e0e4b2cc4be0b9edb5b33e8502'),
('tpex-universe-master','master_snapshot','conservative_first_observed','unknown_without_official_cadence','accepted_master_complete','universe_symbol_mapping_v1','TPEx mopsfin_t187ap03_O',1,'2026-08-21T00:00:00Z','7fd75d94f0e867594ab7c2834cec5c0cd1422b0996198d94395d0b59791a4966'),
('tpex-universe-lifecycle','listing_lifecycle_event','manual_publication_evidence_required','event_observation','explicit_termination_only','universe_symbol_mapping_v1','TPEx delisted/deListed manual publication',1,'2026-08-21T00:00:00Z','a8252cf02efa17cfb0ac44ca594014517096d9d98f28dbb0333496231b5430d3'),
('tpex-universe-operational','trading_operational_event','conservative_first_observed','event_observation','event_only','universe_symbol_mapping_v1','TPEx spendi/cmode',1,'2026-08-21T00:00:00Z','a1261db49fdcfae33d97edc73a227ec37e669ee506a96b91c07c55106c402870'),
('tpex-universe-corroborating','corroborating_identity_observation','manual_publication_evidence_required','event_observation','corroborating_only','universe_symbol_mapping_v1','TPEx company current page',1,'2026-08-21T00:00:00Z','d949c987016490aa4ba03b8110bde71588ee74f62b594f29802e3bfa61a97661')
ON CONFLICT(resource_id) DO NOTHING;

CREATE TRIGGER universe_instruments_no_update BEFORE UPDATE ON universe_instruments BEGIN SELECT RAISE(ABORT,'universe identity anchors are immutable'); END;
CREATE TRIGGER universe_instruments_no_delete BEFORE DELETE ON universe_instruments BEGIN SELECT RAISE(ABORT,'universe identity anchors are immutable'); END;
CREATE TRIGGER universe_resource_policies_no_update BEFORE UPDATE ON universe_resource_policies BEGIN SELECT RAISE(ABORT,'universe resource policies are immutable'); END;
CREATE TRIGGER universe_resource_policies_no_delete BEFORE DELETE ON universe_resource_policies BEGIN SELECT RAISE(ABORT,'universe resource policies are immutable'); END;
CREATE TRIGGER universe_revisions_no_update BEFORE UPDATE ON universe_revisions BEGIN SELECT RAISE(ABORT,'universe revisions are immutable'); END;
CREATE TRIGGER universe_revisions_no_delete BEFORE DELETE ON universe_revisions BEGIN SELECT RAISE(ABORT,'universe revisions are immutable'); END;
CREATE TRIGGER universe_instrument_revisions_no_update BEFORE UPDATE ON universe_instrument_revisions BEGIN SELECT RAISE(ABORT,'universe instrument revisions are immutable'); END;
CREATE TRIGGER universe_instrument_revisions_no_delete BEFORE DELETE ON universe_instrument_revisions BEGIN SELECT RAISE(ABORT,'universe instrument revisions are immutable'); END;
CREATE TRIGGER universe_identity_alias_events_no_update BEFORE UPDATE ON universe_identity_alias_events BEGIN SELECT RAISE(ABORT,'universe aliases are immutable'); END;
CREATE TRIGGER universe_identity_alias_events_no_delete BEFORE DELETE ON universe_identity_alias_events BEGIN SELECT RAISE(ABORT,'universe aliases are immutable'); END;
CREATE TRIGGER universe_lifecycle_events_no_update BEFORE UPDATE ON universe_lifecycle_events BEGIN SELECT RAISE(ABORT,'universe lifecycle events are immutable'); END;
CREATE TRIGGER universe_lifecycle_events_no_delete BEFORE DELETE ON universe_lifecycle_events BEGIN SELECT RAISE(ABORT,'universe lifecycle events are immutable'); END;
CREATE TRIGGER universe_operational_state_events_no_update BEFORE UPDATE ON universe_operational_state_events BEGIN SELECT RAISE(ABORT,'universe operational events are immutable'); END;
CREATE TRIGGER universe_operational_state_events_no_delete BEFORE DELETE ON universe_operational_state_events BEGIN SELECT RAISE(ABORT,'universe operational events are immutable'); END;
CREATE TRIGGER universe_ingestion_idempotency_no_update BEFORE UPDATE ON universe_ingestion_idempotency BEGIN SELECT RAISE(ABORT,'universe idempotency bindings are immutable'); END;
CREATE TRIGGER universe_ingestion_idempotency_no_delete BEFORE DELETE ON universe_ingestion_idempotency BEGIN SELECT RAISE(ABORT,'universe idempotency bindings are immutable'); END;
