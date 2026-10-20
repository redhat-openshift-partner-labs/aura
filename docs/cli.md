# CLI Reference

Run Aura through `uv`:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura COMMAND
```

All successful commands print JSON to standard output.

## Common Flags

| Flag | Required | Default | Acceptable values | Purpose |
| --- | --- | --- | --- | --- |
| `--operator` | Yes | None | Any non-empty operator identifier | Recorded as the command actor |
| `--idempotency-key` | Yes | None | Any non-empty key unique to one operator request | Recorded in the command envelope |
| `--command-dir` | Yes | None | Writable directory | Stores terminal idempotency-key records |
| `--role` | No | `viewer` | `viewer`, `proposer`, `approver`, `executor`, `admin` | Roles used for authorization; may be repeated |
| `--query` | No | Command-specific | Any string | Recorded in command input and CWA interaction slot |
| `--snapshot-dir` | No | None | Writable directory | Store the evidence snapshot |
| `--audit-dir` | No | None | Writable directory | Store audit events |

Phase 1 commands are authorized for `viewer` or `admin`. Other roles are rejected.

Every command stores one terminal idempotency-key record in `--command-dir`: `succeeded`, `failed`, or `refused`. Reusing the same key for the same request exits with code `2` before evidence collection and reports the prior terminal state. Reusing it for a different request is rejected as belonging to a different request. A new attempt requires a new key. Concurrent uses of the same key are serialized by the command store, and only one execution collects evidence.

## `inventory list`

### Source Selection

| Flag | Required | Default | Acceptable values |
| --- | --- | --- | --- |
| `--source` | No | `git` | `git`, `openshift`, `combined` |
| `--fleet-clusters` | Required for `git` and `combined` | None | Path to a local `fleet-clusters` Git checkout with committed definitions |
| `--openshift-kubeconfig` | Required for `openshift` and `combined` | None | Path to a read-only kubeconfig |
| `--cwa-dir` | No | None | Writable directory |
| `--diagnosis-dir` | No | None | Writable directory |
| `--query` | No | `list inventory` | Any string |

Dependency rules:

- `--cwa-dir` requires `--audit-dir`.
- `--diagnosis-dir` requires `--cwa-dir`.
- `--source git` requires `--fleet-clusters`.
- `--source openshift` requires `--openshift-kubeconfig`.
- `--source combined` requires both.

### Git inventory

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source git \
  --fleet-clusters ../fleet-clusters \
  --operator operator@example.com \
  --idempotency-key git-inventory-001 \
  --command-dir ./local/commands
```

Emitted item kinds:

- `cluster_definition`
- `cluster_definition_incomplete`

### OpenShift inventory

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source openshift \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --operator operator@example.com \
  --idempotency-key openshift-inventory-001 \
  --command-dir ./local/commands
```

Emitted item kinds:

- `cluster_actual_state`
- `cluster_actual_state_incomplete`

### Combined inventory

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source combined \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --operator operator@example.com \
  --idempotency-key combined-inventory-001 \
  --command-dir ./local/commands
```

The combined snapshot includes all source items and one `cluster_inventory` item per cluster.

## `workflow inspect`

| Flag | Required | Default | Acceptable values |
| --- | --- | --- | --- |
| `--openshift-kubeconfig` | Yes | None | Path to a read-only kubeconfig |
| `--fleet-clusters` | No | None | Path to a local `fleet-clusters` checkout |
| `--namespace` | No | All namespaces | Existing Tekton namespace |
| `--query` | No | `inspect workflows` | Any string |

Example:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura workflow inspect \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --fleet-clusters ../fleet-clusters \
  --operator operator@example.com \
  --idempotency-key workflow-001 \
  --command-dir ./local/commands
```

Without `--fleet-clusters`, the source is `openshift+tekton`. With it, the source is `git+openshift+tekton`.

Emitted item kinds:

- Git definition items when Git is enabled.
- OpenShift actual-state items.
- `pipeline_run`.
- `workflow_view`.

## `diagnose state`

| Flag | Required | Default | Acceptable values |
| --- | --- | --- | --- |
| `--cluster` | Yes | None | Cluster name |
| `--fleet-clusters` | Yes | None | Path to a local `fleet-clusters` checkout |
| `--openshift-kubeconfig` | Yes | None | Path to a read-only kubeconfig |
| `--cwa-dir` | Yes | None | Writable directory |
| `--audit-dir` | Yes when `--cwa-dir` is used | None | Writable directory |
| `--diagnosis-dir` | No | None | Writable directory |
| `--query` | No | `diagnose cluster state` | Any string |
| `--pipeline-run` | No | Latest matching run | Non-empty PipelineRun name or UID |

Because `--cwa-dir` is required, `--audit-dir` is effectively required for `diagnose state`.

Example:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura diagnose state \
  --cluster 0171d46a-scopedaws \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --snapshot-dir ./local/snapshots \
  --audit-dir ./local/audit \
  --cwa-dir ./local/cwa \
  --diagnosis-dir ./local/diagnoses \
  --operator operator@example.com \
  --idempotency-key diagnosis-001 \
  --command-dir ./local/commands
```

The command returns a `DiagnosisAnswer` containing:

- Diagnosis record.
- Evidence snapshot ID and digest.
- CWA trace ID.

If required evidence is missing, the command exits with code `2`, prints `diagnosis refused:` to standard error, and still stores evidence, CWA, and audit records.

PipelineRun selection uses the latest matching run by `start_time` by default. Pass `--pipeline-run NAME_OR_UID` to select a specific run; if that run is absent, diagnosis is refused with `Selected Tekton PipelineRun`.

## Output and Exit Codes

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `2` | Argument error, unauthorized command, execution failure, diagnosis refusal, duplicate record, or idempotency-key reuse |
| Other non-zero | Unhandled collector or process failure |

An `oc` failure is reported as a concise CLI error with exit code `2`; no partial JSON is printed.

Additional error contracts:

- Blank `--operator` and `--idempotency-key` values are rejected before collection.
- An existing non-directory `--command-dir` is rejected before collection.
- A missing Git checkout is reported as an unusable checkout.
- Git subprocess failures report the exit code and final diagnostic line.
- Missing or inaccessible `oc`, permission failures, invalid JSON, and command timeouts are converted to concise `OpenShiftCLIError` messages.
