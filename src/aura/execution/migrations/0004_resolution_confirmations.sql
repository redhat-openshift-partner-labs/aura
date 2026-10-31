-- Phase 3 execution store, schema version 4: the signed confirmations that
-- a manual resolution of an awaiting_operator execution names.

CREATE TABLE resolution_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX resolution_confirmations_execution
    ON resolution_confirmations (execution_id);

INSERT INTO schema_versions (version) VALUES (4);
