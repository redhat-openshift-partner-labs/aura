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
| `propose cluster-edit` | `propose.patch` | `ProposalService` | `ClusterStateCollector` selecting the latest `post-provision` run |
| `propose post-provision-rerun` | `propose.pipeline` | `PipelineProposalService` | `RerunEvidenceCollector` |
| `propose scaffold` | `propose.patch` | `ProposalService` | `ScaffoldEvidenceCollector` |
| `proposal status` | `proposal.status` | `ProposalStatusService` | `ProposalStalenessCollector` |

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

Git output is decoded from bytes, so file text keeps its line endings. A definition missing a required file is emitted as `cluster_definition_incomplete`; when only `metadata.yaml` is missing, its other files are still validated. A definition whose YAML is malformed, has the wrong document shape, lacks a required patch path, or has a secret-named `metadata.yaml` key is emitted as `cluster_definition_invalid`; other definitions are still collected. Both kinds use the directory name as `cluster_name`. Invalid-YAML errors report only the file and line, never file content.

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

`OpenShiftCLIPipelineClient` is a separate client, so no other read needs Pipeline RBAC:

```bash
oc --kubeconfig KUBECONFIG get pipelines -n openshift-pipelines -o json
```

`PipelineDefinitionCollector` turns each Pipeline, or each named one, into a `pipeline_definition` item, and raises `PipelineDefinitionError` when a named Pipeline is absent. `propose post-provision-rerun` validates the source run's parameters against it.

Every `oc` client declares its resources in `resource_specs`, which the shared `OpenShiftCLIResourceClient` builds.

### Combined cluster inventory

`ClusterInventoryCollector` runs Git, OpenShift, and Tekton collectors, preserves their source items, and appends one `cluster_inventory` item per correlated cluster. Complete, incomplete, and invalid Git definitions all participate in correlation by `cluster_name`; `definition_kind` records which one matched. Deprovision sentinels correlate by the same name and set `deprovision_status`. Combined snapshots keep the source redactions of the items they include.

### Workflow inventory

`WorkflowInventoryCollector` runs OpenShift and Tekton collectors. Git is optional. It preserves source items and appends one `workflow_view` item per PipelineRun.

### Cluster state

`ClusterStateCollector` runs Git, OpenShift, and Tekton collectors for one cluster. It emits all matching Git items, whether complete, incomplete, or invalid, any matching `deprovision_sentinel` items, the matching OpenShift item, the selected PipelineRun, and a `cluster_state_correlation` item.

PipelineRun selection defaults to the latest matching run by `start_time`. The `diagnose state --pipeline-run` flag selects by PipelineRun name or UID. A collector built with `pipeline_name` considers only runs of that Pipeline; `propose cluster-edit` uses `post-provision`. If multiple Git definitions match, the correlation records the conflict and diagnosis refuses to proceed.

`oc` process failures are converted to `OpenShiftCLIError` before they reach command services. The CLI reports the resource, exit code, and final `oc` error line without printing partial JSON.

Each command may make at most 10 `oc` calls. `_execute_command` sets the budget with `oc_call_budget()` around the command, and `_read_oc` spends one call before each `oc` invocation, including through an injected command runner; a cached resource read is not a call. The eleventh call raises `OpenShiftCLIError("oc call budget of 10 exceeded")` without running `oc`, which fails the command. Code outside a command, such as a collector used directly, has no budget.

Git checkout and subprocess failures are converted to `GitInventoryError`. The CLI reports an unusable checkout, the Git exit code, and the final Git diagnostic line without printing partial JSON.

Every external program, `oc`, Git, the scaffold, and `kustomize`, runs through `aura.process.run_process`, and no other module imports `subprocess`; `tests/test_architecture.py` enforces it. `run_process` takes a required environment and timeout, closes standard input unless input is given, never raises on a non-zero exit, returns output as bytes, and raises `ProcessError` when the program cannot start or times out. Each caller maps `ProcessError` and non-zero exits to its own error type. `oc` inherits Aura's environment, every Git call uses `isolated_git_environment()` and a 120-second timeout except the scaffold checkout's 60-second reads, the scaffold gets only `PATH`, `HOME`, and `LANG`, and `kustomize version` inherits the environment.

Process error contracts:

| Source | Failure | CLI behavior |
| --- | --- | --- |
| Git | Missing checkout | Exit `2`; no evidence collected |
| Git | Subprocess failure | Exit `2`; final diagnostic line reported |
| Git | Timeout after 120 seconds | Exit `2`; `git command failed for fleet-clusters:` with the timeout |
| Git | Invalid definition | Collection continues with a `cluster_definition_invalid` item; `diagnose state` for that cluster is refused with `Valid Git cluster definition` |
| `oc` | Missing executable or permission failure | Exit `2`; concise resource-specific error |
| `oc` | Invalid JSON | Exit `2`; no partial JSON emitted |
| `oc` | JSON that is not an object with an `items` list | Exit `2`; `oc returned a response without an item list` for the resource |
| `oc` | Timeout after 30 seconds | Exit `2`; resource-specific timeout reported |
| `oc` | Output that is not UTF-8 | Exit `2`; `oc returned output that is not UTF-8` for the resource |
| `oc` | More than 10 calls in one command | Exit `2`; `oc call budget of 10 exceeded` |
| Stores | Operating-system write failure | Exit `2`; `failed` terminal record stored when the command store is writable |
| Command store | Unreadable record or lock failure | Exit `2` before collection |

### Deprovision proposals

`propose deprovision` collects evidence with `ClusterStateCollector` for the requested cluster, then assembles a CWA snapshot with output contract `proposal`. `DeprovisionProposalGenerator` reads that CWA snapshot and, through `GitPatchCheck`, the `--fleet-clusters` checkout:

1. It takes the cluster from the `cluster_state_correlation` item.
2. It takes `base_commit` from the `git=` revision in the evidence source version.
3. It refuses with every unmet precondition: one definition at `provision/<id>`, no existing sentinel, OpenShift resources present, and a pinned base commit.
4. It refuses if its sentinel patch does not apply to the base commit, checked by `GitPatchCheck` with `require_definition=False` in a `--shared --no-checkout` clone of `--fleet-clusters`.
5. Otherwise it returns a deterministic `Proposal` whose ID derives from the command and CWA snapshot.

The CLI stores the CWA snapshot, the CWA trace, the `context_assembled` and `proposal_created` audit events, and only then the proposal. If any earlier write fails, the command fails and no proposal file exists, so a stored proposal always has its trace and audit events. The trace records provider `aura`, model name `deprovision-sentinel-deterministic`, and the proposal digest as its response digest. Incomplete and invalid definitions do not block a deprovision proposal, because deprovision acts on the cluster name.

### Cluster-edit proposals

`propose cluster-edit` collects cluster state like `propose deprovision`, but selects the latest `post-provision` PipelineRun. `ClusterEditProposalGenerator` reads the CWA snapshot and, through `GitPatchCheck`, the `--fleet-clusters` checkout. It applies the shared live-cluster preconditions, requires a definition that is complete or missing only `metadata.yaml`, and derives each missing key from evidence or the `fleet` `9464f0b` default. It then refuses if `GitPatchCheck`, with `require_definition=True`, finds that the patch does not apply to the base commit or that the result is not one complete definition at `provision/<id>`. The CLI stores records in the same order as for deprovision, with the proposal last.

### Post-provision rerun proposals

`RerunEvidenceCollector` combines `ClusterStateCollector`, selecting the named or latest `post-provision` run, with `PipelineDefinitionCollector` for `post-provision`. `PostProvisionRerunGenerator` reads only the CWA snapshot and rebuilds the PipelineRun from the source run's evidence. The CLI stores records in the same order as for other proposals, with the proposal last.

### Scaffold proposals

`ScaffoldEvidenceCollector` combines `GitInventoryCollector`, ClusterDeployment and ManagedCluster names from `OpenShiftCLIInventoryClient`, the request built from the CLI flags, the scaffold checkout's `HEAD` and cleanliness, and `kustomize_version()`. `ScaffoldProposalGenerator` reads the CWA snapshot, checks preconditions, and only then calls `SubprocessScaffoldRunner`, which exports both repositories with `git ls-tree` and `git cat-file` into a temporary directory, runs the scaffold there, and reports every added file and every other change, which the generator refuses. After its output checks, it builds the patch and calls `GitDefinitionCheck`, which runs `GitPatchCheck` with `require_definition=True`: it applies the patch to a `--shared --no-checkout` clone with `git apply --cached`, commits it with `write-tree`, `commit-tree`, and `update-ref`, and runs `GitInventoryCollector` on the clone. All of these Git calls use the isolation in `aura.git_isolation`. A `ScaffoldRunError` from the runner or the check becomes `ProposalGenerationError`, so the command fails after its CWA snapshot and before its trace, and `command_failed` links to `evidence_collected`. The CLI stores records in the same order as for other proposals, with the proposal last.

### Proposal staleness

`GitProposalStaleness` resolves `HEAD`, runs `git merge-base --is-ancestor BASE HEAD`, and then `git diff --name-only BASE HEAD` limited to the target cluster's paths. All three commands are read-only.

`ProposalStalenessCollector` resolves `HEAD` once and checks every requested proposal, or every `*.json` record in `--proposal-dir` in ID order, against that revision. Loading a record re-verifies its `proposal_digest`. A missing or unreadable store, a misnamed record, an unreadable record, or a tampered record raises `ProposalRecordUnreadable` and fails the command instead of being skipped. Repeated IDs are checked once.

`inventory list`, `workflow inspect`, and `proposal status` are entries in `_EVIDENCE_ROUTES`. Each entry names its service, extra command input, collector selector, and source errors; the service spec supplies the authorization and command-type errors.

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

Stores create restricted parent directories, write and `fsync` private temporary files, and atomically link them into final names. Existing IDs or terminal command keys are treated as errors rather than overwrites, and failed writes leave no partial final records. If the directory sync fails after the record is linked, the store removes the record before raising, so a raised write is never stored. Every store loads through `parse_record`, which rejects a record whose own digest (`record_digest`, `request_digest`, `snapshot_digest`, `event_digest`, `response_digest`, or `proposal_digest`) is blank or missing, because validation would otherwise recompute it, and then verifies the digest. The command store also rejects a record whose idempotency key differs from the key its file name encodes.

The CLI locks `--command-dir`, checks the requested idempotency key, executes the command, and writes the terminal record while holding the lock. A terminal key with the same request digest exits with code `2` before collection. A terminal key with a different request digest is rejected. Failed and refused commands are terminal; a new attempt requires a new key.

`_execute_command` is the CLI's exception boundary. An `Exception` that a route does not handle reaches `_fail_unexpected`, still under the lock, which prints `aura: internal error: <type>: <message>` without a traceback and exits `2`. If a terminal record for the key is already stored, it writes nothing more; if the record at the key is unreadable, it also reports that and writes nothing more. Otherwise it stores the `failed` record and `command_failed` through `_fail_command`, whose stored reason is `internal error: <type>` without the exception text, because that text may carry secret values (I6), linking `command_failed` to the command's stored audit event that no other event names as its predecessor. If `--audit-dir` cannot be read to find that event, it stores only the `failed` record and also reports the read error. `KeyboardInterrupt` and `SystemExit` are not caught.

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

Context, model, proposal, and refusal events reference the previous event ID, forming an audit chain for one command. `command_failed` references the last event that was actually stored, so a failed write never forks the chain.

## Extension Points

| Future need | Existing seam |
| --- | --- |
| New inventory source | Implement `EvidenceCollector` and add CLI wiring |
| New external model | Implement `DiagnosisModel` and pass it through `_diagnosis_record` |
| New record backend | Replace the JSON store while preserving load/save contracts |
| New read-only command | Add `CommandType`, service spec, collector, parser, and tests |
| New proposal type | Add a generator that consumes a `CWASnapshot` and returns a `Proposal`, a `propose` subcommand, and tests |
| Execution adapter | Add a separate Phase 3 guard path; do not extend evidence collectors with writes |
