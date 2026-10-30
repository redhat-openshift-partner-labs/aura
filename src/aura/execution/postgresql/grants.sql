-- Grants for the Phase 3 execution store on PostgreSQL (D13).
-- The database administrator runs this after the migrations, as the owner
-- of the tables. Role aura_operator must exist; each operator logs in as
-- a member of it. The role can insert and select records, and read the
-- schema version and deployment ID; it cannot update, delete, truncate,
-- or change the schema.

GRANT USAGE ON SCHEMA public TO aura_operator;
GRANT SELECT, INSERT ON
    commands,
    audit_events,
    evidence_snapshots,
    published_proposals,
    approvals,
    executions,
    pipeline_run_observations
TO aura_operator;
GRANT SELECT ON schema_versions, deployment TO aura_operator;
-- A row's login is the session's, so one operator cannot claim another's
-- idempotency key.
ALTER TABLE commands
    ADD CONSTRAINT commands_login_is_session_user CHECK (login = CURRENT_USER);
