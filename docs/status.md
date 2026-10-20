# Implementation Status

## Phase 1

Phase 1 functional scope is implemented. Closure hardening is in progress, so Phase 1 is not considered closed until the automation-grade gaps are resolved.

Validation summary:

| Check | Result |
| --- | --- |
| Automated tests | 201 passed |
| Scoped-AWS fixture | `provision/0171d46a-scopedaws` |
| Fixture source commit | `492bc8e07afddcd557441ddb7d72158de60ab8cb` |
| Live OpenShift cluster states | 13 |
| Live Tekton PipelineRuns | 34 |
| Combined inventory records | 16 |
| Correlated workflow views | 34 of 34 |
| Live cluster diagnosis | Fixture cluster diagnosed with four evidence items |
| Non-provisioned refusal | Archived cluster refused with structured missing evidence |
| Duplicate idempotency key | Rejected before collection with no new command record |
| Quality gate | Pending committed baseline during closure hardening |
| Protected repository tracked changes | None |

## Implemented

| Area | Capability |
| --- | --- |
| Git inventory | Reads committed `HEAD` definitions in `provision/` and `archive/`, and marks them `active` or `archived` |
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
| `97c6ece` | Suppressed reuse of completed idempotency keys |
| `447f748` | Validated selectors and reported `oc` failures concisely |
| `7debde5` | Hardened conflict, PipelineRun selection, and non-provisioned diagnosis |
| `9ebfd9b` | Reported active and archived definition conflicts |
| `786ab17` | Recorded Phase 1 validation |
| `f251607` | Read Tekton `cluster-name` parameter |
| `db8f213` | Classified active and archived clusters |
| `b683fea` | Correlated PipelineRuns into workflow views |
| `b26ba5e` | Correlated Git, OpenShift, and Tekton sources |
| `e1c36a5` | Returned diagnosis provenance bundle |
| `bdd49b7` | Refused missing diagnosis evidence |

## Next Work

Do not begin Phase 2 until Phase 1 closure hardening is complete. The remaining closure-hardening sequence is:

1. Enforce restricted store permissions and evidence redaction.
2. Correct diagnosis lifecycle and failed-command provenance.
3. Implement request-bound, concurrency-safe idempotency.
4. Add concise CLI and collector error contracts.
5. Improve source provenance for OpenShift and Tekton.
6. Establish the public remote, CI, and quality baseline.
