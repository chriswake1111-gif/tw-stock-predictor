/* Phase 13 second-review remediation. Additive and transactional only. */

/* Keep raw and normalized evidence independently addressable. The legacy
   payload_sha256 column remains the normalized compatibility value. */
ALTER TABLE universe_revisions ADD COLUMN raw_payload_sha256 TEXT;
ALTER TABLE universe_revisions ADD COLUMN normalized_payload_sha256 TEXT;
ALTER TABLE universe_revisions ADD COLUMN query_dimensions_json TEXT;
ALTER TABLE universe_revisions ADD COLUMN source_record_reference TEXT;
ALTER TABLE universe_revisions ADD COLUMN parser_evidence_fingerprint TEXT;
ALTER TABLE universe_revisions ADD COLUMN schema_evidence_fingerprint TEXT;
ALTER TABLE universe_revisions ADD COLUMN availability_mode TEXT;
ALTER TABLE universe_revisions ADD COLUMN freshness_mode TEXT;
ALTER TABLE universe_revisions ADD COLUMN freshness_status TEXT;
ALTER TABLE universe_revisions ADD COLUMN current_complete INTEGER NOT NULL DEFAULT 0;
ALTER TABLE universe_revisions ADD COLUMN coverage_complete INTEGER NOT NULL DEFAULT 0;

ALTER TABLE universe_instrument_revisions ADD COLUMN raw_resource_revision_id TEXT
    REFERENCES raw_resource_revisions(raw_resource_revision_id);
ALTER TABLE universe_instrument_revisions ADD COLUMN raw_payload_sha256 TEXT;
ALTER TABLE universe_instrument_revisions ADD COLUMN normalized_payload_sha256 TEXT;
ALTER TABLE universe_instrument_revisions ADD COLUMN query_dimensions_json TEXT;
ALTER TABLE universe_instrument_revisions ADD COLUMN source_record_reference TEXT;
ALTER TABLE universe_instrument_revisions ADD COLUMN parser_evidence_fingerprint TEXT;
ALTER TABLE universe_instrument_revisions ADD COLUMN schema_evidence_fingerprint TEXT;

CREATE INDEX idx_universe_revisions_operational_asof
ON universe_revisions(resource_id, logical_revision_key, ingested_at, revision_number);

CREATE INDEX idx_universe_instrument_revisions_raw
ON universe_instrument_revisions(raw_resource_revision_id);

/* TPEx spendi history/today and cmode are kept as distinct logical
   revision-key contracts on the legacy operational resource.  This avoids
   rewriting the immutable seven-row Phase 13 registry while preventing the
   old synthetic key from being used by new collector/parser code. */
SELECT 1;
