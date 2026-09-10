CREATE TABLE local_research_commands (
    command_key TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    result_json TEXT
);
CREATE TRIGGER local_research_command_identity BEFORE UPDATE ON local_research_commands
WHEN NEW.command_key != OLD.command_key OR NEW.request_json != OLD.request_json
 OR NEW.accepted_at != OLD.accepted_at OR OLD.result_json IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'research command identity is immutable'); END;
CREATE TRIGGER local_research_command_no_delete BEFORE DELETE ON local_research_commands
BEGIN SELECT RAISE(ABORT, 'research commands are retained'); END;
