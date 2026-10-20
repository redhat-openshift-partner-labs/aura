# Wiring

## Command Routes

| CLI command | Command type | Service | Default collector |
| --- | --- | --- | --- |
| `inventory list` | `inventory.list` | `InventoryService` | `GitInventoryCollector` |
| `inventory list --source openshift` | `inventory.list` | `InventoryService` | `OpenShiftInventoryCollector` |
| `inventory list --source combined` | `inventory.list` | `InventoryService` | `ClusterInventoryCollector` |
| `workflow inspect` | `workflow.inspect` | `WorkflowService` | `WorkflowInventoryCollector` |
| `diagnose state` | `diagnose.state` | `ClusterStateService` | `ClusterStateCollector` |

All services extend `EvidenceCommandService`. Before collection they:

1. Check operator roles with `AuthorizationPolicy`.
2. Check the command type.
3. Transition the command from `received` to `collecting_evidence`.
4. Call the selected collector.
5. Transition the command to `succeeded`.

## Collector Wiring

### Git

`GitInventoryCollector` reads:

| Directory | Emitted status |
| --- | --- |
| `provision/` | `active` |
| `archive/` | `archived` |

Required files for a complete definition:

- `metadata.yaml`
- `hive/patches/clusterdeployment.yaml`
- `hive/patches/managedcluster.yaml`
- `crossplane/kustomization.yaml`

The source version is the current Git commit from `git rev-parse HEAD`.

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

`ClusterInventoryCollector` runs Git, OpenShift, and Tekton collectors, preserves their source items, and appends one `cluster_inventory` item per correlated cluster.

### Workflow inventory

`WorkflowInventoryCollector` runs OpenShift and Tekton collectors. Git is optional. It preserves source items and appends one `workflow_view` item per PipelineRun.

### Cluster state

`ClusterStateCollector` runs Git, OpenShift, and Tekton collectors for one cluster. It emits all matching Git definitions, the matching OpenShift item, the selected PipelineRun, and a `cluster_state_correlation` item.

PipelineRun selection defaults to the latest matching run by `start_time`. The `diagnose state --pipeline-run` flag selects by PipelineRun name or UID. If multiple Git definitions match, the correlation records the conflict and diagnosis refuses to proceed.

`oc` process failures are converted to `OpenShiftCLIError` before they reach command services. The CLI reports the resource, exit code, and final `oc` error line without printing partial JSON.

## Record Wiring

| Flag | Store | Path pattern |
| --- | --- | --- |
| `--snapshot-dir DIR` | `JsonEvidenceSnapshotStore` | `DIR/SNAPSHOT_ID.json` |
| `--audit-dir DIR` | `JsonAuditEventStore` | `DIR/EVENT_ID.json` |
| `--cwa-dir DIR` | `JsonCWASnapshotStore` | `DIR/snapshots/SNAPSHOT_ID.json` |
| `--cwa-dir DIR` | `JsonCWATraceStore` | `DIR/traces/TRACE_ID.json` |
| `--diagnosis-dir DIR` | `JsonDiagnosisStore` | `DIR/CWA_SNAPSHOT_ID.json` |

Stores create parent directories and open files with exclusive-create mode. Existing IDs are treated as errors rather than overwrites.

## Audit Chain

| Event | Emitted when |
| --- | --- |
| `evidence_collected` | A collector returns a snapshot |
| `context_assembled` | A CWA snapshot and trace are stored |
| `model_called` | A diagnosis is produced |

Context and model events reference the previous event ID, forming an audit chain for one command.

## Extension Points

| Future need | Existing seam |
| --- | --- |
| New inventory source | Implement `EvidenceCollector` and add CLI wiring |
| New external model | Implement `DiagnosisModel` and pass it through `_diagnosis_record` |
| New record backend | Replace the JSON store while preserving load/save contracts |
| New read-only command | Add `CommandType`, service spec, collector, parser, and tests |
| Execution adapter | Add a separate Phase 3 guard path; do not extend evidence collectors with writes |
