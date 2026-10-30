-- Grants for the Phase 3 execution store on PostgreSQL (D13).
-- The database administrator runs this after the migrations, as the owner
-- of the tables. Role aura_operator must exist; each operator logs in as
-- a member of it. The role can insert and select records, and read the
-- schema version and deployment ID; it cannot update, delete, truncate,
-- or change the schema.

GRANT USAGE ON SCHEMA public TO aura_operator;
GRANT SELECT, INSERT ON
    audit_events,
    evidence_snapshots,
    published_proposals,
    approvals,
    executions,
    pipeline_run_observations
TO aura_operator;
GRANT SELECT ON schema_versions, deployment TO aura_operator;
-- A command row's login is always the session's: the column defaults to
-- CURRENT_USER and the role cannot insert it, so one operator cannot
-- claim another's idempotency key. Both statements can be run again.
ALTER TABLE commands ALTER COLUMN login SET DEFAULT CURRENT_USER;
GRANT SELECT ON commands TO aura_operator;
GRANT INSERT (idempotency_key, command_id, record, digest) ON commands TO aura_operator;
