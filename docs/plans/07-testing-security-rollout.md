# Testing, Security, and Rollout

## Status

Aligned on 2026-10-20 with the approved [Phase 2](05-phase-2-proposals.md) plan: proposals are generated deterministically, and no model writes any part of a proposal.

## Testing Strategy

### Contract Tests

Validate:

- Command schemas.
- Evidence schemas.
- Proposal schemas.
- Approval schemas.
- Adapter interfaces.
- Audit event schemas.

### Collector Tests

Use recorded fixtures from:

- `fleet-clusters`, including `deprovision/` sentinels with and without the `-archive` suffix.
- Tekton Pipeline definitions read from the hub.
- OpenShift resource responses.
- Tekton PipelineRun responses, including parameters and workspace bindings.

Collectors must be deterministic and testable without a live cluster. Correlation tests that depend on Git item shapes use the real `GitInventoryCollector` over a temporary Git tree.

### Proposal Generator Tests

Required cases:

| Case | Expected result |
| --- | --- |
| Scaffold proposal | Patch adds only `provision/<id>/**`, applies with `git apply --check`, and yields one complete `cluster_definition` |
| Scaffold for an existing name | Refuse |
| Deprovision proposal | Exactly one added file, `deprovision/<id>-archive`, in a standalone commit |
| Deprovision with any existing sentinel | Refuse |
| Edit that adds a file under `provision/<id>` | Commit message contains `[skip-provision]` |
| Edit value without evidence | Uses the `fleet` default and names it as the value's source; evidence that contradicts an existing value refuses (D1 in the [closure plan](10-phase-0-2-closure.md#operator-decisions)) |
| Post-provision rerun with a missing required parameter | Refuse |
| Post-provision rerun with an unknown or redacted parameter | Refuse |
| Patch touching two clusters | Refuse |
| Base commit moved for the target cluster | Report stale |
| Scaffold output outside `provision/<id>/` or containing a symlink | Refuse |
| Generated content altered by redaction | Refuse |

Each lifecycle fact in the [Phase 2](05-phase-2-proposals.md#lifecycle-facts) table has a test asserting the `trigger_effect` the generator reports.

### Guard Tests

Required cases:

| Case | Expected result |
| --- | --- |
| Valid proposal and authorized approver | Dispatch |
| Missing approval | Reject |
| Wrong approver role | Reject |
| Modified proposal after approval | Reject |
| Duplicate idempotency key | Do not dispatch twice |
| Unknown adapter | Reject |
| Out-of-scope target | Reject |
| Stale proposal | Reject |
| Proposal touching two clusters | Reject |
| Missing required commit message | Reject |
| Bare deprovision sentinel | Reject |
| Direct PipelineRun for pre-provision, provision, or deprovision | Reject |
| EventListener as the execution path | Reject |
| Proposal with invalid schema | Reject |

### Failure-Mode and Invariant Tests

Every command follows the [development workflow](../development.md). Required:

- A failure test for each row of the command's failure-mode matrix, asserting the exit code, the terminal record, that no actionable artifact remains, and the `command_failed` link.
- Tests for each project invariant the command touches, including the linear audit chain and artifacts-written-last rules.
- Negative tests against the contract for every rule that says only, exactly, empty, must, must not, or never.
- Input edge tests for missing, unreadable, duplicate, misnamed, and tampered inputs.

### Adapter Tests

Use local fake Git and Tekton servers.

Required assertions:

1. Adapter receives only approved input.
2. Adapter cannot access unrelated resources.
3. Adapter records result and failure reason.
4. Adapter does not expose secrets.
5. Adapter can be safely retried after an ambiguous failure.

### Model Evaluation

Phases 1 and 2 use deterministic models and generators, so no model evaluation set gates them. Before any external model is integrated, after explicit operator approval and a data-flow review, evaluation sets must include:

| Category | Examples |
| --- | --- |
| Inventory questions | List active clusters, tier, region, status |
| Diagnosis | Stuck provisioning, failed PipelineRun, drift, stranded deprovision |
| Refusal | Missing evidence, ambiguous target, unsafe request |
| Injection resistance | Malicious instructions in logs, parameters, or documentation |

A model never generates patches, sentinels, or PipelineRun parameters.

Every evaluation case must record:

- Input query.
- Frozen evidence snapshot.
- CWA trace.
- Model output.
- Guard decision.
- Expected outcome.

## Security Requirements

1. Secrets never enter model context. PipelineRun parameters whose names contain a secret term are redacted unless explicitly allowlisted.
2. Secret names may be shown only when useful and safe.
3. Model output is untrusted until validated.
4. Authorization is enforced outside the model.
5. Adapters use separate least-privilege credentials.
6. Audit events are append-only.
7. Git push and Tekton execution require explicit approval. Aura never pushes to `main`, never merges, and never calls the fleet EventListener.
8. All external calls are bounded. In Phases 0–2 each command has a budget of 10 `oc` calls, which are the hub calls, and every external call has a timeout; full rate limiting is a Phase 3 requirement (D3 in the [closure plan](10-phase-0-2-closure.md#operator-decisions)).
9. All proposal changes are recorded with a digest.
10. Model provider access is isolated from cluster credentials.

## Rollout Gates

| Gate | Requirement |
| --- | --- |
| Gate 1 | Contract and collector tests pass |
| Gate 2 | Read-only assistant works against a hub with the read-only service account. With no staging hub, the production hub qualifies for read-only phases; staging is required before any Phase 3 execution (D2 in the [closure plan](10-phase-0-2-closure.md#operator-decisions)) |
| Gate 3 | Deterministic proposals pass generator tests and validate against the `fleet-clusters` fixture and live read-only evidence |
| Gate 4 | Guard blocks all unauthorized execution |
| Gate 5 | Approved Git patch execution succeeds in a test repository |
| Gate 6 | Approved Tekton execution succeeds in staging |
| Gate 7 | Audit trail reproduces every action |
| Gate 8 | Operator runbook and rollback procedure are complete |

## Rollout Plan

1. Run Aura locally with read-only access.
2. Connect to a staging OpenShift cluster.
3. Generate patches without applying them.
4. Execute only against a disposable Git fork, which has no webhook to the hub EventListener.
5. Execute a staging Tekton pipeline.
6. Enable one production operation at a time.
7. Keep manual operation available as the fallback.

## Rollback

Aura can be disabled without affecting:

- Git repositories.
- OpenShift cluster state.
- Tekton pipelines.
- Portal.

If Aura is disabled during execution:

1. Stop accepting new commands.
2. Let in-flight adapters finish or record an ambiguous state.
3. Inspect the audit trail.
4. Resume manually using the existing operator process.

