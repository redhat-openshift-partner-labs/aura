# Testing, Security, and Rollout

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

- `fleet-clusters`.
- `fleet` pipeline definitions.
- OpenShift resource responses.
- Tekton PipelineRun responses.

Collectors must be deterministic and testable without a live cluster.

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
| Model output with invalid schema | Reject |

### Adapter Tests

Use local fake Git and Tekton servers.

Required assertions:

1. Adapter receives only approved input.
2. Adapter cannot access unrelated resources.
3. Adapter records result and failure reason.
4. Adapter does not expose secrets.
5. Adapter can be safely retried after an ambiguous failure.

### Model Evaluation

Evaluation sets should include:

| Category | Examples |
| --- | --- |
| Inventory questions | List active clusters, tier, region, status |
| Diagnosis | Stuck provisioning, failed PipelineRun, drift |
| Patch generation | New cluster, modify metadata, deprovision plan |
| Refusal | Missing evidence, ambiguous target, unsafe request |
| Injection resistance | Malicious instructions in logs or documentation |

Every evaluation case must record:

- Input query.
- Frozen evidence snapshot.
- CWA trace.
- Model output.
- Guard decision.
- Expected outcome.

## Security Requirements

1. Secrets never enter model context.
2. Secret names may be shown only when useful and safe.
3. Model output is untrusted until validated.
4. Authorization is enforced outside the model.
5. Adapters use separate least-privilege credentials.
6. Audit events are append-only.
7. Git push and Tekton execution require explicit approval.
8. All external calls are rate limited.
9. All proposal changes are recorded with a digest.
10. Model provider access is isolated from cluster credentials.

## Rollout Gates

| Gate | Requirement |
| --- | --- |
| Gate 1 | Contract and collector tests pass |
| Gate 2 | Read-only assistant works against staging |
| Gate 3 | Proposal generation produces correct patches |
| Gate 4 | Guard blocks all unauthorized execution |
| Gate 5 | Approved Git patch execution succeeds in a test repository |
| Gate 6 | Approved Tekton execution succeeds in staging |
| Gate 7 | Audit trail reproduces every action |
| Gate 8 | Operator runbook and rollback procedure are complete |

## Rollout Plan

1. Run Aura locally with read-only access.
2. Connect to a staging OpenShift cluster.
3. Generate patches without applying them.
4. Execute only against a disposable Git fork.
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

