CREATE TABLE research_watchlist_items (
    watchlist_item_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    membership_state TEXT NOT NULL CHECK (membership_state IN ('active','archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    workflow_contract_version TEXT NOT NULL
        CHECK (workflow_contract_version = 'research_review_queue_v1'),
    CHECK (
        (membership_state = 'active' AND archived_at IS NULL)
        OR (membership_state = 'archived' AND archived_at IS NOT NULL)
    )
);

CREATE INDEX idx_research_watchlist_state_symbol
ON research_watchlist_items(membership_state, symbol);

CREATE TRIGGER research_watchlist_no_delete
BEFORE DELETE ON research_watchlist_items
BEGIN
    SELECT RAISE(ABORT, 'research_watchlist_items are archive-only');
END;

CREATE TRIGGER research_watchlist_identity_immutable
BEFORE UPDATE ON research_watchlist_items
WHEN NEW.watchlist_item_id IS NOT OLD.watchlist_item_id
  OR NEW.symbol IS NOT OLD.symbol
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.workflow_contract_version IS NOT OLD.workflow_contract_version
BEGIN
    SELECT RAISE(ABORT, 'research watchlist identity is immutable');
END;

CREATE TABLE research_review_events (
    review_event_id TEXT PRIMARY KEY,
    watchlist_item_id TEXT NOT NULL,
    acknowledged_snapshot_id TEXT NOT NULL,
    comparison_cutoff_at TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    workflow_contract_version TEXT NOT NULL
        CHECK (workflow_contract_version = 'research_review_queue_v1'),
    FOREIGN KEY (watchlist_item_id) REFERENCES research_watchlist_items(watchlist_item_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (acknowledged_snapshot_id) REFERENCES analysis_snapshots(snapshot_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX idx_research_review_latest
ON research_review_events(
    watchlist_item_id,
    reviewed_at DESC,
    created_at DESC,
    review_event_id DESC
);

CREATE INDEX idx_research_review_snapshot
ON research_review_events(acknowledged_snapshot_id);

CREATE TRIGGER research_review_events_no_update
BEFORE UPDATE ON research_review_events
BEGIN
    SELECT RAISE(ABORT, 'research_review_events are append-only');
END;

CREATE TRIGGER research_review_events_no_delete
BEFORE DELETE ON research_review_events
BEGIN
    SELECT RAISE(ABORT, 'research_review_events are append-only');
END;
