-- Independent retrospective evidence; never promotes legacy calendar or model data.
CREATE TABLE wave_session_evidence (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    source_id TEXT NOT NULL,
    scope_start TEXT NOT NULL,
    scope_end TEXT NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    fetched_at TEXT NOT NULL,
    previous_id TEXT REFERENCES wave_session_evidence(record_id),
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_bytes INTEGER NOT NULL CHECK(payload_bytes > 0),
    CHECK(scope_start <= scope_end)
);
CREATE INDEX wave_session_evidence_scope ON wave_session_evidence
    (symbol, source_id, scope_start, scope_end, sequence);
CREATE TRIGGER wave_session_evidence_no_update BEFORE UPDATE ON wave_session_evidence
BEGIN SELECT RAISE(ABORT, 'wave session evidence is immutable'); END;
CREATE TRIGGER wave_session_evidence_no_delete BEFORE DELETE ON wave_session_evidence
BEGIN SELECT RAISE(ABORT, 'wave session evidence is immutable'); END;
