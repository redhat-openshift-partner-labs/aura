-- Phase 3 execution store, schema version 2: execution records and
-- PipelineRun observations.

CREATE TABLE executions (
    record_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    cluster_name TEXT NOT NULL,
    phase TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX executions_execution ON executions (execution_id);
CREATE INDEX executions_cluster ON executions (cluster_name);
-- A proposal is dispatched at most once.
CREATE UNIQUE INDEX executions_dispatch
    ON executions (proposal_id) WHERE phase = 'dispatching';
-- An execution has at most one terminal record.
CREATE UNIQUE INDEX executions_terminal
    ON executions (execution_id)
    WHERE phase IN ('handed_off', 'succeeded', 'failed', 'merged', 'closed', 'not_dispatched');

CREATE TABLE pipeline_run_observations (
    observation_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    record TEXT NOT NULL,
    digest TEXT NOT NULL,
    stored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX pipeline_run_observations_execution
    ON pipeline_run_observations (execution_id);

INSERT INTO schema_versions (version) VALUES (2);
