# Implementation Status

Phases 0, 1, and 2 are closed: the operator signed them off on 2026-10-26, accepting every caveat in the [Closure Grades](plans/10-phase-0-2-closure.md#closure-grades). Current test and `act` results are in [Validation](validation.md).

## Phase 1

Phase 1 functional scope and closure hardening are implemented and validated. Phase 1 is published: `main` was pushed to the public remote at `01f0f38`, and CI run `39474106779` passed. The `metadata.yaml` and `platform` backfills were resolved in `fleet-clusters` on 2026-10-25; the stranded deprovision sentinels remain open in [Open Questions](plans/08-open-questions.md#fleet-clusters-data), and Aura reports them and does not work around them.

Validation summary, recorded at Aura commit `6dc0eb3`:

| Check | Result |
| --- | --- |
| Automated tests | 270 passed |
| Quality gate | `adb2c2b..HEAD` passed with zero gating findings |
| Scoped-AWS fixture | `provision/<cluster-name>` |
| Fixture source commit | `492bc8e07afddcd557441ddb7d72158de60ab8cb` |
| Git definitions | 26: 12 complete, 14 incomplete, 0 invalid |
| Live OpenShift cluster states | 13 |
| Live Tekton PipelineRuns | 34 |
| Combined inventory records | 25 |
| Combined inventory evidence items | 98 |
| Combined drift | 1 `none`, 6 `incomplete-metadata`, 5 `incomplete-definition`, 6 `missing-openshift`, 6 `archived`, 1 `definition-conflict`, 0 `detected`, 0 `missing-git` |
| Workflow evidence items | 107 |
| Correlated workflow views | 34 of 34 |
| Live cluster diagnosis | Two clusters diagnosed, each citing three evidence items |
| Refusals | Incomplete, archived non-provisioned, and conflicting definitions refused with structured missing evidence |
| Duplicate idempotency key | Rejected before collection with no standard output |
| Read-only RBAC | `list` allowed; `create`, `patch`, and `delete` denied |
| Continuous integration | Run `39474106779` passed at `01f0f38`; run `39473619779` at `8c91797` failed on an order-dependent test fixed in `b841594` |
| Record durability | No `.tmp` files remained after fixture and live validation |
| Protected repository tracked changes | None |

## Phase 2 Prerequisites

Prerequisites P0, P1, P3, and P4 of the approved [Phase 2](plans/05-phase-2-proposals.md) plan are implemented. P2's Pipeline definition collector is implemented; its namespaced Role and RoleBinding in [`deploy/`](../deploy) were applied and validated on 2026-10-24. P3 adds the `Proposal` contract, `JsonProposalStore`, and `GitProposalStaleness`. P4 adds `propose deprovision`, authorized for `proposer` and `admin`, which generates the deprovision sentinel proposal type. `proposal status` reports Git staleness of stored proposals for `viewer`, `proposer`, and `admin`. The operator published through `6496375`, and CI run `40120149984` passed. `propose cluster-edit` generates `metadata.yaml` backfill proposals; the operator validated it against the live hub on 2026-10-24. `propose scaffold` generates new-cluster proposals from a pinned local `fleet-cluster-scaffold` checkout and applies them to a clone of `fleet-clusters` to confirm a complete definition; it was validated against the live hub on 2026-10-25 with a successful proposal and the in-use and infra-prefix refusals. `propose post-provision-rerun` generates rerun proposals; the operator validated a refusal on 2026-10-24 and a successful proposal on 2026-10-25 against the live hub. The latest test, quality-gate, and `act` results are recorded only in [Validation](validation.md). An earlier record claimed zero gating findings at `0ba7124`; that gate run had analyzed the previous commit, and the committed tree had six findings, fixed in `ca37a83`. The shared failure-injection matrix and invariant check cover every command. The operator validated `propose deprovision` against the live hub on 2026-10-23 with `<cluster-name>`; see [Live Proposal Validation](validation.md#live-proposal-validation). Live validation of P0 and P1, recorded at Aura commit `655a0ac`:

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
| Proposal staleness | Checks stored proposals against one pinned `fleet-clusters` `HEAD` and fails on tampered or malformed records; Git-only, for every proposal type including reruns |
| Authorization | Allows Phase 1 commands for `viewer` and `admin`, `propose` commands for `proposer` and `admin`, and `proposal status` for `viewer`, `proposer`, and `admin`; supplied `--role` values replace the `viewer` default |
| Cluster-edit proposals | Generates a deterministic proposal that adds `metadata.yaml` with the `fleet` keys the cluster's tier needs, `openshift-ai-version` only for `ai`, or appends the missing keys, from ClusterDeployment, post-provision PipelineRun, or `fleet` default values; refuses on any contradictory evidence |
| Pipeline definitions | Reads which parameters each Pipeline in `openshift-pipelines` declares and requires, through a separate client; stores no default values, counts malformed entries, and fails on missing or unnamed Pipelines |
| Post-provision rerun proposals | Copies a finished `post-provision` run's parameters, workspaces, and run template into an unapplied PipelineRun manifest, checked against the live Pipeline definition and `metadata.yaml`; refuses on any unmet precondition |
| CLI | Provides `inventory list`, `workflow inspect`, `diagnose state`, `propose deprovision`, `propose cluster-edit`, `propose post-provision-rerun`, `propose scaffold`, and `proposal status` |

## Current Model Behavior

The current `diagnose state` model is deterministic and local:

- Provider: `aura`
- Model: `cluster-state-deterministic`
- External model provider: not wired
- Network model call: none

The model protocol remains provider-independent. A future provider must consume a CWA snapshot and return the strict `DiagnosisResponse` JSON contract.

## Phase 3

The operator approved the [Phase 3](plans/06-phase-3-approved-execution.md) plan on 2026-10-27 with decisions D7–D13, and confirmed its Stage 1 plan edits on 2026-10-29. Stage 1 hardening is done; its [Stage 1 Record](plans/06-phase-3-approved-execution.md#stage-1-record) lists each task's commits and test evidence. Stage 1 changed this behavior:

- An unhandled error stores only its type in `command_failed`; an unhandled `OSError` with an errno is a filesystem error.
- A refusal path that fails after `command_refused` stores a `refused` record, and another command's malformed audit record no longer blocks `command_failed`.
- Stores revalidate each record before writing it, CWA traces carry `trace_digest`, and `aura.provenance` verifies cross-record IDs and digests.
- Stored Git proposals and their CWA traces record the patch-check outcome.
- A failed `oc` read is not retried within a command, and an `oc` list without `metadata.resourceVersion` is an error.
- Scaffold output is limited to 1 MiB per stream, 1 MiB per generated file, and 8 MiB of generated files in total.

`deploy/postgresql/` deploys the staging PostgreSQL for the Phase 3 execution store. Stage 2 added the execution store on SQLite, Aura's configuration file, SSH signing against the hub signer list, the action digest, `proposal submit`, `proposal approve`, and `proposal reject`, and the derived state in `proposal status`. Stage 3 added the Command Guard G1–G12, G8's regeneration of a proposal from fresh evidence, `execute` with the test-only `recording` adapter, the structural test that pins adapters and credential reads, and the PostgreSQL backend with its grants file. Stage 4 adds the Tekton adapter, which creates the approved post-provision PipelineRun, the `aura-executor` manifests in `deploy/executor/`, and `execution status` and `execution confirm`, which observe PipelineRuns and resolve an `awaiting_operator` execution; none has run against a hub, which task 4.4 does. The operator confirmed Stage 3's plan edits on 2026-10-30; the [Stage 3 Record](plans/06-phase-3-approved-execution.md#stage-3-record) lists them.

## Not Implemented

- External model provider integration.
- Natural-language chat interface.
- Applying patches, creating commits, or opening pull requests (Phase 3 Stage 5).
- Pipeline or Git execution (Phase 3 Stages 4 and 5).
- Portal integration.
- RabbitMQ integration or replacement.
- Long-running daemon, scheduler, or web UI.
- Database-backed stores for Phases 0–2: their records stay in append-only JSON files. Phase 3 records are in the execution store, on SQLite or PostgreSQL.
- Meaningful OpenShift and Tekton source versions: `oc get -o json` returns a `List` with an empty `resourceVersion`, so those `source_version` values are empty, for example `pipelines=`.

## Recent Implementation Commits

| Commit | Change |
| --- | --- |
| `8b13d07` | Stored G8's evidence, CWA records, and regenerated proposal |
| `58762da` | Took the command login from the PostgreSQL session; PostgreSQL contract tests |
| `111cf36` | Caught aliased adapter calls and credential reads |
| `a7c159c` | Closed the Stage 3 review's guard and execute findings |
| `d0155cd` | Added the PostgreSQL backend for the execution store |
| `1b05cf8` | Pinned where adapters live and who calls them |
| `2f6e685` | Added `execute`, dispatching through the guard |
| `0a84abd` | Regenerated a proposal from fresh evidence for G8 |
| `d6d12f7` | Added the Command Guard, G1–G12 |
| `566c7df` | Added execution record, observation, and adapter contracts |
| `3c3e054` | Resolved import aliases in the process and parsing chokepoint rules |
| `2171460` | Hashed scaffold files in chunks and reported scaffold start failures |
| `1de5003` | Stored only the type of an errno-less unhandled `OSError` |
| `c74d7d3` | Recorded the Git patch-check outcome on proposals and CWA traces |
| `8dc07a5` | Limited scaffold stream and file sizes |
| `71d82cc` | Kept failed `oc` reads and required list resource versions |
| `ab480a9` | Rejected `os` calls that start programs outside `process.py` |
| `3689b4c` | Discovered contracts and stores in the digest tests |
| `d88ab1c` | Verified cross-record digests and added CWA trace digests |
| `b989aff` | Revalidated records on save and pinned the record loader |
| `4889513` | Reported unhandled OS errors and missing working directories precisely |
| `9beb53d` | Kept audit chains intact on unhandled-error paths |
| `7e2fca9` | Stored only the error type for unhandled errors |
| `88b4668` | Added staging PostgreSQL manifests |
| `01df26b` | Skipped the rerun metadata check without one definition |
| `75d149b` | Failed cleanly when a generated proposal breaks the contract |
| `17d7fa3` | Refused rerun overlap and foreign source runs |
| `5029b4b` | Mirrored rerun allow-lists in the contract |
| `21c703e` | Tolerated zero values and reported unfinished runs |
| `590d242` | Added the `propose post-provision-rerun` command |
| `6783e45` | Generated post-provision rerun proposals |
| `436291d` | Tightened the post-provision rerun contract |
| `fe72bd5` | Recorded PipelineRun run templates for reruns |
| `9ca2a10` | Added read-only OpenShift RBAC manifests in `deploy/` |
| `fcd86d1` | Failed closed on Pipeline definitions and stopped storing defaults |
| `d6ef7b7` | Read Tekton Pipeline parameter definitions |
| `dfe32d4` | Required whole-file append-only metadata edits |
| `a749dac` | Kept exact metadata text and validated remaining files |
| `9ae9a18` | Added the `propose cluster-edit` command |
| `2a79664` | Generated cluster-edit metadata backfill proposals |
| `0756229` | Restricted cluster edits to appending metadata keys |
| `72576ac` | Recorded metadata sources for cluster edits |
| `8326732` | Made permission tests root-safe for `act` |
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

Phases 0–2 are closed. Next is [Phase 3](plans/06-phase-3-approved-execution.md), revised on 2026-10-27: its operator decisions D7–D13 are recorded, including a database for Phase 3 records (SQLite for development, PostgreSQL in production), and the operator approved the plan on 2026-10-27. Stages 1–3 are done, and Stage 4, the Tekton adapter, is next. Its decisions, D9 and D10, are recorded; its live run, task 4.4, needs the staging hub, `aura-executor`, and staging store that the operator provides under task 0.3; the plan follows its staged tasks under the [Phase 3 Entry Conditions](plans/10-phase-0-2-closure.md#phase-3-entry-conditions): E2–E4 before any Phase 3 execution and E1 before an external model is connected. Deferred items, including the stranded deprovision sentinels and legacy rerun records, are tracked only in the closure plan's [Backlog](plans/10-phase-0-2-closure.md#backlog).
