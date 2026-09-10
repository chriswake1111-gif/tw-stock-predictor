CREATE TABLE daily_public_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    dataset TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    UNIQUE(symbol, dataset, raw_sha256, parser_version)
);
CREATE INDEX daily_public_asof ON daily_public_snapshots(symbol, dataset, observed_at);
CREATE TABLE daily_public_attempts (
    attempt_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    dataset TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('available','failed')),
    reason TEXT,
    snapshot_id TEXT REFERENCES daily_public_snapshots(snapshot_id)
);
CREATE INDEX daily_public_attempt_recent ON daily_public_attempts(symbol, dataset, checked_at);
CREATE TRIGGER daily_public_snapshot_no_update BEFORE UPDATE ON daily_public_snapshots
BEGIN SELECT RAISE(ABORT, 'daily public snapshots are immutable'); END;
CREATE TRIGGER daily_public_snapshot_no_delete BEFORE DELETE ON daily_public_snapshots
BEGIN SELECT RAISE(ABORT, 'daily public snapshots are immutable'); END;
