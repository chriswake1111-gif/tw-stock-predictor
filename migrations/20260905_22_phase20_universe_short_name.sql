-- Phase 20: Installed Product Usability & Research Bootstrap V1
-- Additive short_name column on immutable universe_instrument_revisions.

ALTER TABLE universe_instrument_revisions ADD COLUMN short_name TEXT;

CREATE INDEX IF NOT EXISTS idx_universe_instrument_revisions_short_name
ON universe_instrument_revisions(short_name);

UPDATE data_resources
SET parser_version = '2.0.0'
WHERE resource_id IN ('twse-universe-master', 'tpex-universe-master');
