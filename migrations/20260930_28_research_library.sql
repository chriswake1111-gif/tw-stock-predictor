CREATE TABLE research_holding_labels (
    symbol TEXT PRIMARY KEY,
    is_held INTEGER NOT NULL CHECK (is_held IN (0,1)),
    revision INTEGER NOT NULL CHECK (revision > 0),
    updated_at TEXT NOT NULL
);
CREATE TABLE research_library_commands (
    command_key TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
