-- Phase 3 execution store, schema version 3: the CWA records and the
-- proposal that execute's G8 regeneration writes.

CREATE TABLE cwa_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX cwa_snapshots_command ON cwa_snapshots (command_id);

CREATE TABLE cwa_traces (
    trace_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX cwa_traces_command ON cwa_traces (command_id);

CREATE TABLE regenerated_proposals (
    proposal_id TEXT PRIMARY KEY,
    cwa_trace_id TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX regenerated_proposals_trace ON regenerated_proposals (cwa_trace_id);

INSERT INTO schema_versions (version) VALUES (3);
