-- Phase 3 execution store, schema version 1.
-- SQL that SQLite and PostgreSQL both accept. Each row holds its record's
-- canonical JSON as text, never jsonb, and copies of the fields that
-- constraints and queries use. No row is ever updated or deleted.

CREATE TABLE schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE deployment (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    deployment_id TEXT NOT NULL
);

CREATE TABLE commands (
    login TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_id TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (login, idempotency_key)
);

CREATE TABLE audit_events (
    event_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    previous_event_id TEXT,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX audit_events_command ON audit_events (command_id);
CREATE UNIQUE INDEX audit_events_successor
    ON audit_events (previous_event_id) WHERE previous_event_id IS NOT NULL;

CREATE TABLE evidence_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE published_proposals (
    publication_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    cluster_name TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    principal TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX approvals_proposal ON approvals (proposal_id);

INSERT INTO schema_versions (version) VALUES (1);
