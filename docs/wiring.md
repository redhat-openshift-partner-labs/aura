# Wiring

## Command Routes

| CLI command | Command type | Service | Default collector |
| --- | --- | --- | --- |
| `inventory list` | `inventory.list` | `InventoryService` | `GitInventoryCollector` |
| `inventory list --source openshift` | `inventory.list` | `InventoryService` | `OpenShiftInventoryCollector` |
| `inventory list --source combined` | `inventory.list` | `InventoryService` | `ClusterInventoryCollector` |
| `workflow inspect` | `workflow.inspect` | `WorkflowService` | `WorkflowInventoryCollector` |
| `diagnose state` | `diagnose.state` | `ClusterStateService` | `ClusterStateCollector` |
| `propose deprovision` | `propose.patch` | `ProposalService` | `ClusterStateCollector` |

All services extend `EvidenceCommandService`. Before collection they:

1. Check operator roles with `AuthorizationPolicy`.
2. Check the command type.
3. Transition the command from `received` to `collecting_evidence`.
4. Call the selected collector.
5. Transition the command to `assembling_context`.

The CLI owns the final terminal transition to `succeeded`, `failed`, or `refused` after collection, optional CWA assembly, diagnosis, persistence, and output.

## Collector Wiring

### Git

`GitInventoryCollector` resolves `HEAD` once, then reads both the tree listing and file blobs from that pinned revision:

| Directory | Emitted status |
| --- | --- |
| `provision/` | `active` |
| `archive/` | `archived` |

Required files for a complete definition:

- `metadata.yaml`
- `hive/patches/clusterdeployment.yaml`
- `hive/patches/managedcluster.yaml`
- `crossplane/kustomization.yaml`

A definition missing a required file is emitted as `cluster_definition_incomplete`. A definition whose YAML is malformed, has the wrong document shape, or lacks a required patch path is emitted as `cluster_definition_invalid`; other definitions are still collected. Both kinds use the directory name as `cluster_name`. Invalid-YAML errors report only the file and line, never file content.

Top-level files under `deprovision/`, other than `README.md`, are emitted as `deprovision_sentinel` items. The sentinel `deprovision/<id>-archive` names cluster `<id>` with `archive_suffix: true`; a bare `deprovision/<id>` has `archive_suffix: false`.

Required patch paths:

| File | Paths |
| --- | --- |
| `hive/patches/clusterdeployment.yaml` | `/metadata/name`, `/spec/platform/aws/region`, `/spec/provisioning/imageSetRef/name` |
| `hive/patches/managedcluster.yaml` | `/metadata/labels/tier`, `/metadata/labels/environment` |

The source version is the pinned commit. Working-tree modifications, later commits, and untracked files are not evidence.

### OpenShift

`OpenShiftCLIInventoryClient` uses:

```bash
oc --kubeconfig KUBECONFIG get clusterdeployments -A -o json
oc --kubeconfig KUBECONFIG get managedclusters -o json
```

### Tekton

`OpenShiftCLITektonClient` uses:

```bash
oc --kubeconfig KUBECONFIG get pipelineruns -A -o json
oc --kubeconfig KUBECONFIG get taskruns -A -o json
```

With `workflow inspect --namespace NAMESPACE`, both commands use `-n NAMESPACE` instead of `-A`.

PipelineRun correlation first reads `spec.params` where `name` is `cluster-name`. Namespace correlation is a fallback for runs that do not provide that parameter.

### Combined cluster inventory

`ClusterInventoryCollector` runs Git, OpenShift, and Tekton collectors, preserves their source items, and appends one `cluster_inventory` item per correlated cluster. Complete, incomplete, and invalid Git definitions all participate in correlation by `cluster_name`; `definition_kind` records which one matched. Deprovision sentinels correlate by the same name and set `deprovision_status`. Combined snapshots keep the source redactions of the items they include.

### Workflow inventory

`WorkflowInventoryCollector` runs OpenShift and Tekton collectors. Git is optional. It preserves source items and appends one `workflow_view` item per PipelineRun.

### Cluster state

`ClusterStateCollector` runs Git, OpenShift, and Tekton collectors for one cluster. It emits all matching Git items, whether complete, incomplete, or invalid, any matching `deprovision_sentinel` items, the matching OpenShift item, the selected PipelineRun, and a `cluster_state_correlation` item.

PipelineRun selection defaults to the latest matching run by `start_time`. The `diagnose state --pipeline-run` flag selects by PipelineRun name or UID. If multiple Git definitions match, the correlation records the conflict and diagnosis refuses to proceed.

`oc` process failures are converted to `OpenShiftCLIError` before they reach command services. The CLI reports the resource, exit code, and final `oc` error line without printing partial JSON.

Git checkout and subprocess failures are converted to `GitInventoryError`. The CLI reports an unusable checkout, the Git exit code, and the final Git diagnostic line without printing partial JSON.

Process error contracts:

| Source | Failure | CLI behavior |
| --- | --- | --- |
| Git | Missing checkout | Exit `2`; no evidence collected |
| Git | Subprocess failure | Exit `2`; final diagnostic line reported |
| Git | Invalid definition | Collection continues with a `cluster_definition_invalid` item; `diagnose state` for that cluster is refused with `Valid Git cluster definition` |
| `oc` | Missing executable or permission failure | Exit `2`; concise resource-specific error |
| `oc` | Invalid JSON | Exit `2`; no partial JSON emitted |
| `oc` | Timeout after 30 seconds | Exit `2`; resource-specific timeout reported |
| Stores | Operating-system write failure | Exit `2`; `failed` terminal record stored when the command store is writable |
| Command store | Unreadable record or lock failure | Exit `2` before collection |

### Deprovision proposals

`propose deprovision` collects evidence with `ClusterStateCollector` for the requested cluster, then assembles a CWA snapshot with output contract `proposal`. `DeprovisionProposalGenerator` reads only that CWA snapshot:

1. It takes the cluster from the `cluster_state_correlation` item.
2. It takes `base_commit` from the `git=` revision in the evidence source version.
3. It refuses with every unmet precondition: one definition at `provision/<id>`, no existing sentinel, OpenShift resources present, and a pinned base commit.
4. Otherwise it returns a deterministic `Proposal` whose ID derives from the command and CWA snapshot.

The CLI stores the CWA snapshot, the proposal, and then the CWA trace. The trace records provider `aura`, model name `deprovision-sentinel-deterministic`, and the proposal digest as its response digest. Incomplete and invalid definitions do not block a deprovision proposal, because deprovision acts on the cluster name.

### Proposal staleness

`GitProposalStaleness` resolves `HEAD`, runs `git merge-base --is-ancestor BASE HEAD`, and then `git diff --name-only BASE HEAD` limited to the target cluster's paths. All three commands are read-only.

## Record Wiring

| Flag | Store | Path pattern |
| --- | --- | --- |
| `--command-dir DIR` | `JsonCommandStore` | `DIR/SHA256_OF_KEY.json` |
| `--snapshot-dir DIR` | `JsonEvidenceSnapshotStore` | `DIR/SNAPSHOT_ID.json` |
| `--audit-dir DIR` | `JsonAuditEventStore` | `DIR/EVENT_ID.json` |
| `--cwa-dir DIR` | `JsonCWASnapshotStore` | `DIR/snapshots/SNAPSHOT_ID.json` |
| `--cwa-dir DIR` | `JsonCWATraceStore` | `DIR/traces/TRACE_ID.json` |
| `--diagnosis-dir DIR` | `JsonDiagnosisStore` | `DIR/CWA_SNAPSHOT_ID.json` |
| `--proposal-dir DIR` | `JsonProposalStore` | `DIR/PROPOSAL_ID.json` |

Stores create restricted parent directories, write and `fsync` private temporary files, and atomically link them into final names. Existing IDs or terminal command keys are treated as errors rather than overwrites, and failed writes leave no partial final records.

The CLI locks `--command-dir`, checks the requested idempotency key, executes the command, and writes the terminal record while holding the lock. A terminal key with the same request digest exits with code `2` before collection. A terminal key with a different request digest is rejected. Failed and refused commands are terminal; a new attempt requires a new key.

## Audit Chain

| Event | Emitted when |
| --- | --- |
| `command_received` | The CLI starts a command after the idempotency-key check |
| `evidence_collected` | A collector returns a snapshot |
| `context_assembled` | A CWA snapshot and trace are stored |
| `model_called` | A diagnosis is produced |
| `proposal_created` | A proposal is stored |
| `command_failed` | A command reaches the terminal failed state |
| `command_refused` | A diagnosis or proposal command reaches the terminal refused state |

Context, model, proposal, and refusal events reference the previous event ID, forming an audit chain for one command.

## Extension Points

| Future need | Existing seam |
| --- | --- |
| New inventory source | Implement `EvidenceCollector` and add CLI wiring |
| New external model | Implement `DiagnosisModel` and pass it through `_diagnosis_record` |
| New record backend | Replace the JSON store while preserving load/save contracts |
| New read-only command | Add `CommandType`, service spec, collector, parser, and tests |
| New proposal type | Add a generator that consumes a `CWASnapshot` and returns a `Proposal`, a `propose` subcommand, and tests |
| Execution adapter | Add a separate Phase 3 guard path; do not extend evidence collectors with writes |
