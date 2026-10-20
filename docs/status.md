# Implementation Status

## Phase 1

Phase 1 is complete and validated. The implementation provides a read-only assistant with immutable evidence and CWA provenance.

Validation summary:

| Check | Result |
| --- | --- |
| Automated tests | 192 passed |
| Scoped-AWS fixture | `provision/0171d46a-scopedaws` |
| Fixture source commit | `492bc8e07afddcd557441ddb7d72158de60ab8cb` |
| Live OpenShift cluster states | 13 |
| Live Tekton PipelineRuns | 34 |
| Combined inventory records | 16 |
| Correlated workflow views | 34 of 34 |
| Protected repository tracked changes | None |

## Implemented

| Area | Capability |
| --- | --- |
| Git inventory | Reads `provision/` and `archive/`, and marks definitions `active` or `archived` |
| Incomplete definitions | Reports missing required files without discarding definition status |
| OpenShift inventory | Reads `ClusterDeployment` and `ManagedCluster` resources |
| Tekton inventory | Reads `PipelineRun` and `TaskRun` resources and failed-task summaries |
| Cluster correlation | Combines Git, OpenShift, and Tekton evidence by cluster name and reports conflicting definitions |
| Workflow correlation | Uses Tekton `spec.params` `cluster-name` and namespace fallback |
| Cluster inventory | Emits desired state, actual state, provisioning status, latest run, and drift |
| Workflow view | Emits run status, failed task, related cluster, and next diagnostic step |
| Cluster-state diagnosis | Correlates evidence for one requested cluster and selects the latest PipelineRun by default |
| Refusal | Refuses diagnosis when required evidence is missing, definitions conflict, or a selected PipelineRun is absent |
| Non-provisioned diagnosis | Reports the OpenShift `Provisioned` condition without claiming the cluster is deployed |
| CWA | Assembles all nine required slots and computes a snapshot digest |
| Provenance | Links evidence, CWA snapshot, CWA trace, diagnosis, and audit events |
| Stores | Uses append-only JSON stores for evidence, CWA, audit, and diagnosis records |
| Idempotency | Suppresses reuse of keys belonging to completed commands |
| Authorization | Allows Phase 1 commands for `viewer` and `admin` roles |
| CLI | Provides `inventory list`, `workflow inspect`, and `diagnose state` |

## Current Model Behavior

The current `diagnose state` model is deterministic and local:

- Provider: `aura`
- Model: `cluster-state-deterministic`
- External model provider: not wired
- Network model call: none

The model protocol remains provider-independent. A future provider must consume a CWA snapshot and return the strict `DiagnosisResponse` JSON contract.

## Not Implemented

- External model provider integration.
- Natural-language chat interface.
- Patch or PR generation.
- Pipeline or Git execution.
- Portal integration.
- RabbitMQ integration or replacement.
- Long-running daemon, scheduler, or web UI.
- Database-backed stores; current stores are append-only JSON files.

## Recent Implementation Commits

| Commit | Change |
| --- | --- |
| `7debde5` | Hardened conflict, PipelineRun selection, and non-provisioned diagnosis |
| `9ebfd9b` | Reported active and archived definition conflicts |
| `786ab17` | Recorded Phase 1 validation |
| `f251607` | Read Tekton `cluster-name` parameter |
| `db8f213` | Classified active and archived clusters |
| `b683fea` | Correlated PipelineRuns into workflow views |
| `b26ba5e` | Correlated Git, OpenShift, and Tekton sources |
| `e1c36a5` | Returned diagnosis provenance bundle |
| `bdd49b7` | Refused missing diagnosis evidence |

## Next Phase

Phase 2 should begin with proposals only:

1. Define proposal contracts.
2. Add deterministic patch and pipeline proposal generators.
3. Add guard validation.
4. Keep execution disabled.
5. Continue test-driven development and incremental conventional commits.
