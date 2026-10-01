-- Isolated provenance packages; never financial observations, approvals or signals.
CREATE TABLE wave_candidate_packages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    previous_id TEXT REFERENCES wave_candidate_packages(package_id),
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_bytes INTEGER NOT NULL CHECK(payload_bytes > 0)
);
CREATE INDEX wave_candidate_packages_symbol ON wave_candidate_packages(symbol, sequence);
CREATE TRIGGER wave_candidate_packages_no_update BEFORE UPDATE ON wave_candidate_packages
BEGIN SELECT RAISE(ABORT, 'wave candidate package is immutable'); END;
CREATE TRIGGER wave_candidate_packages_no_delete BEFORE DELETE ON wave_candidate_packages
BEGIN SELECT RAISE(ABORT, 'wave candidate package is immutable'); END;
