# Validation

## Automated Tests

Run:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
```

Expected current result:

```text
201 passed
```

Run diff hygiene:

```bash
git diff --check
```

Run the structural quality gate:

```bash
ripwire . --quality-delta
```

Without a committed baseline, this compares the working tree with `HEAD`; a clean tree therefore reports zero regressions but does not evaluate already-committed quality. A committed baseline is pending closure hardening.

The test suite covers:

- Command and state contracts.
- Authorization.
- Git, OpenShift, and Tekton collectors.
- Cluster and workflow correlation.
- Cluster-state diagnosis.
- Refusal when evidence is missing.
- CWA assembly and digests.
- Immutable JSON stores.
- CLI argument combinations and output.
- Fixture validity and secret redaction.

## Local Fixture

Fixture cluster:

```text
provision/0171d46a-scopedaws
```

Validated `fleet-clusters` commit:

```text
492bc8e07afddcd557441ddb7d72158de60ab8cb
```

Validate Git inventory:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source git \
  --fleet-clusters ../fleet-clusters \
  --operator operator@example.com \
  --idempotency-key fixture-git-001 \
  --command-dir ./local/commands
```

Check that output contains:

- `provision/0171d46a-scopedaws`.
- `status: active`.
- `cluster_name: 0171d46a-scopedaws`.
- Archived definitions with `status: archived`.

## Live OpenShift Validation

Use the isolated read-only kubeconfig:

```bash
export AURA_KUBECONFIG="$HOME/.kube/aura-readonly"
```

Validate OpenShift inventory:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source openshift \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --operator operator@example.com \
  --idempotency-key live-openshift-001 \
  --command-dir ./local/commands
```

Validate combined inventory:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source combined \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --operator operator@example.com \
  --idempotency-key live-combined-001 \
  --command-dir ./local/commands
```

Validate workflow correlation:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura workflow inspect \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --fleet-clusters ../fleet-clusters \
  --operator operator@example.com \
  --idempotency-key live-workflow-001 \
  --command-dir ./local/commands
```

Latest validation observed:

| Metric | Value |
| --- | --- |
| OpenShift cluster states | 13 |
| PipelineRuns | 34 |
| Combined inventory records | 16 |
| Workflow views | 34 |
| Correlated workflow views | 34 |
| Uncorrelated workflow views | 0 |

## Live Diagnosis Validation

Validate the scoped-AWS fixture:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura diagnose state \
  --cluster 0171d46a-scopedaws \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --snapshot-dir ./local/snapshots \
  --audit-dir ./local/audit \
  --cwa-dir ./local/cwa \
  --diagnosis-dir ./local/diagnoses \
  --operator operator@example.com \
  --idempotency-key live-diagnosis-001 \
  --command-dir ./local/commands
```

Latest validation observed:

| Check | Result |
| --- | --- |
| Fixture diagnosis | Exit code `0`; latest PipelineRun reported as `Succeeded` |
| Evidence items | 1 definition, 1 actual state, 1 PipelineRun, 1 correlation |
| Provenance | Evidence, CWA snapshot, CWA trace, diagnosis, and audit records linked by matching IDs and digests |
| Non-provisioned cluster | `1bd62dc5-hpezerto` refused with `OpenShift actual state` and `Tekton PipelineRun status` missing; no diagnosis record was written |

## Idempotency Validation

Repeat a command with an idempotency key that has already reached a terminal state.

Expected result:

- Exit code `2`.
- Standard error contains `aura: idempotency key already succeeded`.
- No JSON is written to standard output.
- No new command, evidence, CWA, or audit record is created.

Failed and refused commands create terminal records. A new attempt requires a new idempotency key.

## Record Persistence Validation

Use disposable local directories:

```bash
validation=/tmp/aura-validation
mkdir -p "$validation"/{snapshots,audit,cwa}
```

Freeze combined evidence:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source combined \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --snapshot-dir "$validation/snapshots" \
  --audit-dir "$validation/audit" \
  --cwa-dir "$validation/cwa" \
  --operator operator@example.com \
  --idempotency-key records-001 \
  --command-dir "$validation/commands"
```

Expected files:

| Directory | Count |
| --- | --- |
| `commands/` | 1 |
| `snapshots/` | 1 |
| `audit/` | 3 |
| `cwa/snapshots/` | 1 |
| `cwa/traces/` | 1 |

Check that:

- Evidence snapshot digest matches its file content.
- CWA trace references the same evidence snapshot ID and digest.
- CWA trace references the stored CWA snapshot ID and digest.
- Audit events reference the command and records.
- No `.tmp` files remain in any record directory.

## Protected Repository Validation

From the `aura` repository:

```bash
for repository in fleet fleet-cluster-scaffold fleet-clusters portal; do
  git -C "../$repository" diff --exit-code
done
```

There may be unrelated untracked files in those checkouts. The validation requirement is no tracked modifications.

## Regression Rules

Before calling documentation or implementation complete:

1. Add a failing test for the new behavior.
2. Implement the smallest correct change.
3. Run the focused test.
4. Run the full suite.
5. Run `git diff --check`.
6. Validate affected collectors against the local fixture.
7. Run live validation only when explicitly approved.
8. Confirm protected repositories have no tracked changes.
