# Implementation Status

## Phase 1

Phase 1 functional scope and closure hardening are implemented and validated. Publication to the public remote is the remaining operational step.

Validation summary:

| Check | Result |
| --- | --- |
| Automated tests | 237 passed |
| Scoped-AWS fixture | `provision/0171d46a-scopedaws` |
| Fixture source commit | `492bc8e07afddcd557441ddb7d72158de60ab8cb` |
| Live OpenShift cluster states | 13 |
| Live Tekton PipelineRuns | 34 |
| Combined inventory records | 16 |
| Combined inventory evidence items | 89 |
| Workflow evidence items | 107 |
| Correlated workflow views | 34 of 34 |
| Live cluster diagnosis | Fixture cluster diagnosed with four evidence items |
| Non-provisioned refusal | Archived cluster refused with structured missing evidence |
| Duplicate idempotency key | Request-bound and concurrency-safe rejection before collection |
| Quality gate | `adb2c2b..HEAD` passed with zero gating findings |
| Record durability | No `.tmp` files remained after fixture and live validation |
| Protected repository tracked changes | None |

## Implemented

| Area | Capability |
| --- | --- |
| Git inventory | Reads committed `HEAD` definitions in `provision/` and `archive/`, and marks them `active` or `archived` |
| Git provenance | Resolves `HEAD` once and pins tree and blob reads to that revision |
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
| Stores | Uses atomically published, append-only JSON records for evidence, CWA, audit, and diagnosis data |
| Idempotency | Binds keys to one request and suppresses concurrent or sequential terminal-key reuse |
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
| `7e2ce61` | Removed digest and audit duplication |
| `983f147` | Closed full-range quality regressions |
| `8f8e069` | Inlined audit persistence |
| `a3694b3` | Removed store-error duplication |
| `6acb880` | Reduced command duplication |
| `1c62c7a` | Added refused lifecycle digests |
| `6848efd` | Published JSON records atomically |
| `660227e` | Pinned Git evidence to resolved `HEAD` |
| `8f73ad5` | Hardened CLI and collector error contracts |
| `68108c4` | Enforced request-bound command lifecycle |
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

Do not begin Phase 2 until Phase 1 is published. The remaining sequence is:

1. Push `main` to the public remote.
