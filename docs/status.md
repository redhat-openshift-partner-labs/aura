# Implementation Status

## Phase 1

Phase 1 functional scope and closure hardening are implemented and validated. Phase 1 is published: `main` was pushed to the public remote at `01f0f38`, and CI run `39474106779` passed. Two `fleet-clusters` data issues remain open in [Open Questions](plans/08-open-questions.md#fleet-clusters-data); Aura reports them rather than working around them.

Validation summary, recorded at Aura commit `6dc0eb3`:

| Check | Result |
| --- | --- |
| Automated tests | 270 passed |
| Quality gate | `adb2c2b..HEAD` passed with zero gating findings |
| Scoped-AWS fixture | `provision/0171d46a-scopedaws` |
| Fixture source commit | `492bc8e07afddcd557441ddb7d72158de60ab8cb` |
| Git definitions | 26: 12 complete, 14 incomplete, 0 invalid |
| Live OpenShift cluster states | 13 |
| Live Tekton PipelineRuns | 34 |
| Combined inventory records | 25 |
| Combined inventory evidence items | 98 |
| Combined drift | 1 `none`, 6 `incomplete-metadata`, 5 `incomplete-definition`, 6 `missing-openshift`, 6 `archived`, 1 `definition-conflict`, 0 `detected`, 0 `missing-git` |
| Workflow evidence items | 107 |
| Correlated workflow views | 34 of 34 |
| Live cluster diagnosis | `0171d46a-scopedaws` and `2dcb6934-thalescs` diagnosed, each citing three evidence items |
| Refusals | Incomplete, archived non-provisioned, and conflicting definitions refused with structured missing evidence |
| Duplicate idempotency key | Rejected before collection with no standard output |
| Read-only RBAC | `list` allowed; `create`, `patch`, and `delete` denied |
| Continuous integration | Run `39474106779` passed at `01f0f38`; run `39473619779` at `8c91797` failed on an order-dependent test fixed in `b841594` |
| Record durability | No `.tmp` files remained after fixture and live validation |
| Protected repository tracked changes | None |

## Phase 2 Prerequisites

Prerequisites P0, P1, P3, and P4 of the approved [Phase 2](plans/05-phase-2-proposals.md) plan are implemented; P2 remains. P3 adds the `Proposal` contract, `JsonProposalStore`, and `GitProposalStaleness`. P4 adds `propose deprovision`, authorized for `proposer` and `admin`, which generates the deprovision sentinel proposal type. `proposal status` reports Git staleness of stored proposals for `viewer`, `proposer`, and `admin`. Automated tests at `b32a67b`: 480 passed, with zero gating quality findings. The shared failure-injection matrix and invariant check cover every command. The operator validated `propose deprovision` against the live hub on 2026-10-23 with `virt-cookbook`; see [Live Proposal Validation](validation.md#live-proposal-validation). Live validation of P0 and P1, recorded at Aura commit `655a0ac`:

| Check | Result |
| --- | --- |
| Automated tests | 295 passed |
| Quality gate | `adb2c2b..HEAD` passed with zero gating findings |
| Deprovision sentinels | 7 read from `fleet-clusters`, all without the `-archive` suffix |
| Combined inventory | 25 records and 105 evidence items; 6 `stranded`, 1 `leftover`, no `missing-openshift` |
| Stranded diagnosis | All 6 stranded clusters diagnosed; 4 have no retained PipelineRun |
| PipelineRun parameters | Recorded for all 34 PipelineRuns; no live value redacted after allowlisting `htpasswd-provider-name` |
| Workflow evidence items | 114 |
| Protected repository tracked changes | None |

## Implemented

| Area | Capability |
| --- | --- |
| Git inventory | Reads committed `HEAD` definitions in `provision/` and `archive/`, and marks them `active` or `archived` |
| Git provenance | Resolves `HEAD` once and pins tree and blob reads to that revision |
| Incomplete definitions | Reports missing required files with definition status and directory-derived cluster name; correlates them in combined inventory and diagnosis |
| Invalid definitions | Reports malformed YAML, wrong document shapes, and missing required patch paths per definition without aborting collection |
| Legacy metadata | Reports `incomplete-metadata` drift when `metadata.yaml` has no `platform` and other compared fields match |
| Deprovision sentinels | Reads `deprovision/` and reports pending, archive-pending, stranded, leftover, and orphaned deprovisions in inventory and diagnosis |
| PipelineRun parameters | Records parameters and workspace bindings, redacting secret-named parameters outside an allowlist |
| OpenShift inventory | Reads `ClusterDeployment` and `ManagedCluster` resources |
| Tekton inventory | Reads `PipelineRun` and `TaskRun` resources and failed-task summaries |
| Cluster correlation | Combines Git, OpenShift, and Tekton evidence by cluster name and reports conflicting definitions |
| Workflow correlation | Uses Tekton `spec.params` `cluster-name` and namespace fallback |
| Cluster inventory | Emits desired state, actual state, provisioning status, latest run, and drift |
| Workflow view | Emits run status, failed task, related cluster, and next diagnostic step |
| Cluster-state diagnosis | Correlates evidence for one requested cluster and selects the latest PipelineRun by default |
| Refusal | Refuses diagnosis when required evidence is missing, a definition is incomplete or invalid, definitions conflict, or a selected PipelineRun is absent |
| PipelineRun outcomes | Names the described PipelineRun and reports `failed`, `succeeded`, or `has not completed`; an incomplete run is never reported as succeeded |
| Non-provisioned diagnosis | Reports the OpenShift `Provisioned` condition without claiming the cluster is deployed |
| CWA | Assembles all nine required slots and computes a snapshot digest |
| Provenance | Links evidence, CWA snapshot, CWA trace, diagnosis, and audit events |
| Stores | Uses atomically published, append-only JSON records for evidence, CWA, audit, and diagnosis data; write failures become `failed` commands |
| Redaction | Matches secret field names across case and separator variants |
| Idempotency | Binds keys to one request and suppresses concurrent or sequential terminal-key reuse |
| Deprovision proposals | Generates a deterministic, digest-verified proposal that adds only `deprovision/<id>-archive` as a standalone commit, or refuses with every unmet precondition; stores proposal, CWA, and `proposal_created` audit records and writes nothing to Git |
| Proposal staleness | Checks stored proposals against one pinned `fleet-clusters` `HEAD` and fails on tampered or malformed records; Git-only |
| Authorization | Allows Phase 1 commands for `viewer` and `admin`, `propose` commands for `proposer` and `admin`, and `proposal status` for `viewer`, `proposer`, and `admin`; supplied `--role` values replace the `viewer` default |
| CLI | Provides `inventory list`, `workflow inspect`, `diagnose state`, `propose deprovision`, and `proposal status` |

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
- Scaffold, cluster-edit, and post-provision rerun proposals.
- Applying patches, creating commits, or opening pull requests.
- Pipeline or Git execution.
- Portal integration.
- RabbitMQ integration or replacement.
- Long-running daemon, scheduler, or web UI.
- Database-backed stores; current stores are append-only JSON files.

## Recent Implementation Commits

| Commit | Change |
| --- | --- |
| `b32a67b` | Hardened the failure matrix and invariant checks |
| `17b06d3` | Removed a linked record when the directory sync fails |
| `33fc43f` | Added the failure-injection matrix and invariant checks |
| `5465797` | Stored proposals last and linked failures to stored audit events |
| `554b1c6` | Required an empty regular-file deprovision sentinel |
| `d81056b` | Failed proposal status on unreadable stores |
| `cfaba98` | Acknowledged evidence route false positives |
| `69a3c80` | Added the `proposal status` command |
| `b4be1f7` | Collected staleness of stored proposals |
| `c5abafb` | Added the `propose deprovision` command |
| `b8f20fa` | Generated deprovision sentinel proposals |
| `ec84fd4` | Acknowledged store exception false positives |
| `bdb5864` | Shared JSON record store behavior |
| `cbd7b2f` | Reported stale Git proposals |
| `029f8c4` | Added the append-only proposal store |
| `b60308a` | Added the proposal contract |
| `655a0ac` | Explained deprovision without retained PipelineRuns |
| `3e9fff4` | Explained deprovision of legacy definitions |
| `25fed6b` | Kept the htpasswd provider name parameter |
| `53b5bd6` | Recorded redacted PipelineRun parameters |
| `559076c` | Explained deprovision requests in diagnosis |
| `b71aeab` | Reported deprovision sentinels |
| `b841594` | Selected the workflow audit event by type instead of glob order |
| `6dc0eb3` | Folded metadata drift cases into the drift test |
| `5c00ab0` | Collapsed command lookup failures |
| `c162da8` | Shared test collection helpers and fakes |
| `1f1bb22` | Hoisted stateless helpers out of collectors |
| `ba44ea6` | Distinguished incomplete metadata from drift |
| `2e518a3` | Matched secret field spelling variants |
| `20b460e` | Reported record store failures without tracebacks |
| `31ee2ab` | Reported incomplete PipelineRuns as not completed |
| `2b894fe` | Reported invalid definitions as evidence |
| `2d02a87` | Correlated incomplete definitions by directory name |
| `1f9877e` | Replaced the default role with supplied roles |
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

Phase 1 is published. Remaining work:

1. `fleet-clusters` owners backfill `metadata.yaml` and its `platform` key for the definitions listed in [Open Questions](plans/08-open-questions.md#fleet-clusters-data), pushing any added `metadata.yaml` with `[skip-provision]` in the head commit message, and decide how to remediate the stranded deprovision sentinels.
2. Implement the remaining [Phase 2](plans/05-phase-2-proposals.md) prerequisite P2, then the scaffold, cluster-edit, and post-provision rerun proposal types, each under the development workflow. P0, P1, P3, and P4 are done.
