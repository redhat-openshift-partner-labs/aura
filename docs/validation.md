# Validation

## Automated Tests

Run:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
```

Expected current result:

```text
295 passed
```

Run diff hygiene:

```bash
git diff --check
```

Run the structural quality gate:

```bash
ripwire . --quality-delta=adb2c2b..HEAD
```

Expected current result: zero gating findings. The committed range is the quality baseline through the Phase 1 closure changes.

The test suite covers:

- Command and state contracts.
- Authorization.
- Git, OpenShift, and Tekton collectors, including incomplete and invalid Git definitions.
- Cluster and workflow correlation.
- Cluster-state diagnosis, including failed, succeeded, and not-completed PipelineRuns.
- Refusal when evidence is missing.
- CWA assembly and digests.
- Immutable JSON stores, record write failures, and unreadable command records.
- CLI argument combinations and output.
- Fixture validity and secret redaction, including field spelling variants.

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
- 26 definitions at the validated commit: 12 `cluster_definition`, 14 `cluster_definition_incomplete`, and no `cluster_definition_invalid`.
- Every incomplete definition has a `cluster_name` equal to its directory name.
- 7 `deprovision_sentinel` items, all with `archive_suffix: false`.

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
| Combined inventory records | 25 |
| Combined inventory evidence items | 105 |
| `missing-git`, `missing-openshift`, or `detected` drift | 0 |
| `incomplete-definition` drift | 5 |
| `incomplete-metadata` drift | 6 |
| `deprovision-requested` drift | 6, all `deprovision_status: stranded` |
| `leftover` deprovision status | 1, `f6f3eba2-redisvirt` |
| Combined snapshot redactions | None; no live parameter name contains a non-allowlisted secret term |
| Workflow evidence items | 114 |
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

Run the same command with these clusters and new idempotency keys to validate refusals:

| Cluster | Expected result |
| --- | --- |
| `2dcb6934-thalescs` | Exit code `0`; summary names a succeeded PipelineRun |
| `423d216c-vistergy` | Exit code `2`; refused with `Git cluster definition files` and `Tekton PipelineRun status` |
| `1bd62dc5-hpezerto` | Exit code `2`; refused with `OpenShift actual state` and `Tekton PipelineRun status` |
| `pchriste` | Exit code `2`; refused with `Conflicting Git cluster definitions` |
| `10f25620-vscopeocpv` | Exit code `0`; stranded deprovision summary citing its retained deprovision PipelineRun, confidence `0.9` |
| `264c5202-codvoqs` | Exit code `0`; stranded deprovision summary stating `no PipelineRun is retained for it`, confidence `0.6` |
| `f6f3eba2-redisvirt` | Exit code `2`; leftover sentinel, refused with `Git cluster definition files` and `OpenShift actual state` |

Latest validation observed:

| Check | Result |
| --- | --- |
| Fixture diagnosis | Exit code `0`; summary names PipelineRun `post-provision-0171d46a-scopedaws-p4w4v` as succeeded with confidence `0.9` |
| Evidence items | Snapshot holds 1 definition, 1 actual state, 1 PipelineRun, and 1 correlation; the response cites the first three |
| Provenance | Evidence, CWA snapshot, CWA trace, diagnosis, and audit records linked by matching IDs and digests |
| Refusals | All four refusal clusters exited `2` with the expected missing evidence; no diagnosis record was written for them |
| Stranded deprovisions | All six stranded clusters exited `0`: two cite a retained deprovision PipelineRun, four state that no PipelineRun is retained |

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
