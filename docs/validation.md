# Validation

## Automated Tests

Run:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
```

Expected current result:

```text
180 passed
```

Run diff hygiene:

```bash
git diff --check
```

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
  --idempotency-key fixture-git-001
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
  --idempotency-key live-openshift-001
```

Validate combined inventory:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source combined \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --operator operator@example.com \
  --idempotency-key live-combined-001
```

Validate workflow correlation:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura workflow inspect \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --fleet-clusters ../fleet-clusters \
  --operator operator@example.com \
  --idempotency-key live-workflow-001
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
  --idempotency-key records-001
```

Expected files:

| Directory | Count |
| --- | --- |
| `snapshots/` | 1 |
| `audit/` | 2 |
| `cwa/snapshots/` | 1 |
| `cwa/traces/` | 1 |

Check that:

- Evidence snapshot digest matches its file content.
- CWA trace references the same evidence snapshot ID and digest.
- CWA trace references the stored CWA snapshot ID and digest.
- Audit events reference the command and records.

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
