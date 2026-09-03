-- Phase 19: Installed Data Synchronization / Local Data Operations V1
-- Additive tables for parent data operation lifecycle and child item tracking.

CREATE TABLE installed_data_operations (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK (
        operation_type IN ('sync', 'bootstrap', 'enable_symbol')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'cancelling', 'succeeded', 'partial', 'failed', 'cancelled', 'interrupted')
    ),
    current_stage TEXT NOT NULL CHECK (
        current_stage IN ('prerequisites_calendar', 'universe', 'classification', 'eod', 'turnover_and_cbc', 'projection', 'idle', 'completed')
    ),
    lease_owner_id TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    target_symbols_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error_detail TEXT
);

CREATE TABLE installed_data_operation_items (
    item_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES installed_data_operations(operation_id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (
        stage IN ('prerequisites_calendar', 'universe', 'classification', 'eod', 'turnover_and_cbc', 'projection')
    ),
    resource_id TEXT NOT NULL REFERENCES data_resources(resource_id),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'accepted', 'partial', 'failed', 'skipped', 'schema_changed', 'unrecognized')
    ),
    raw_resource_revision_id TEXT REFERENCES raw_resource_revisions(raw_resource_revision_id),
    ingestion_run_id TEXT REFERENCES ingestion_runs(ingestion_run_id),
    ingestion_run_item_id TEXT REFERENCES ingestion_run_items(ingestion_run_item_id),
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count >= 1),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error_code TEXT,
    error_detail TEXT
);

CREATE INDEX idx_installed_data_operations_status
ON installed_data_operations(status, created_at);

CREATE INDEX idx_installed_data_operations_lease
ON installed_data_operations(lease_owner_id, lease_expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_installed_data_operations_single_active
ON installed_data_operations ((1)) WHERE status IN ('pending', 'running', 'cancelling');

CREATE INDEX idx_installed_data_operation_items_op
ON installed_data_operation_items(operation_id, stage, item_id);

CREATE INDEX idx_installed_data_operation_items_resource
ON installed_data_operation_items(resource_id, status);
