CREATE TABLE daily_research_entries (
    entry_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    knowledge_cutoff_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    analysis_snapshot_id TEXT REFERENCES analysis_snapshots(snapshot_id)
);
CREATE INDEX daily_research_entry_history ON daily_research_entries(symbol,created_at);
CREATE TRIGGER daily_research_entry_no_update BEFORE UPDATE ON daily_research_entries
BEGIN SELECT RAISE(ABORT,'saved daily research is immutable'); END;
CREATE TRIGGER daily_research_entry_no_delete BEFORE DELETE ON daily_research_entries
BEGIN SELECT RAISE(ABORT,'saved daily research is retained'); END;
