CREATE TABLE research_evidence_records (
    record_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    series_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    previous_id TEXT REFERENCES research_evidence_records(record_id),
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    payload_bytes INTEGER NOT NULL CHECK(payload_bytes > 0),
    UNIQUE(series_id, revision),
    UNIQUE(symbol, content_sha256)
);
CREATE INDEX research_evidence_by_symbol ON research_evidence_records(symbol, recorded_at, record_id);
CREATE TRIGGER research_evidence_no_update BEFORE UPDATE ON research_evidence_records
BEGIN SELECT RAISE(ABORT, 'research evidence versions are immutable'); END;
CREATE TRIGGER research_evidence_no_delete BEFORE DELETE ON research_evidence_records
BEGIN SELECT RAISE(ABORT, 'research evidence is retained'); END;
CREATE TABLE research_evidence_requests (
    request_key TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES research_evidence_records(record_id)
);
CREATE TRIGGER research_evidence_requests_no_update BEFORE UPDATE ON research_evidence_requests
BEGIN SELECT RAISE(ABORT, 'research evidence request is immutable'); END;
CREATE TRIGGER research_evidence_requests_no_delete BEFORE DELETE ON research_evidence_requests
BEGIN SELECT RAISE(ABORT, 'research evidence request is retained'); END;
