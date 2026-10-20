# Evidence, CWA, Audit, and Diagnosis Records

## Evidence Snapshot

An `EvidenceSnapshot` is the frozen result of one collector invocation.

| Field | Meaning |
| --- | --- |
| `snapshot_id` | UUID for the evidence snapshot |
| `command_id` | UUID for the command that requested collection |
| `collector` | Collector identifier |
| `collector_version` | Collector implementation version |
| `collected_at` | UTC collection timestamp |
| `source` | Human-readable source or joined sources |
| `source_version` | Pinned Git commit or joined resource versions |
| `items` | Typed evidence items |
| `redactions` | Fields removed before storage |
| `snapshot_digest` | SHA-256 digest of canonical snapshot JSON |

## Evidence Item Kinds

| Kind | Producer | Meaning |
| --- | --- | --- |
| `cluster_definition` | Git | Complete active or archived desired-state definition |
| `cluster_definition_incomplete` | Git | Definition directory with missing required files; `cluster_name` is the directory name |
| `cluster_definition_invalid` | Git | Definition with all required files whose YAML or required patch paths cannot be interpreted; `cluster_name` is the directory name and `error` names the file and problem |
| `cluster_actual_state` | OpenShift | Complete observed cluster state |
| `cluster_actual_state_incomplete` | OpenShift | Cluster resource with missing status data |
| `pipeline_run` | Tekton | PipelineRun and summarized TaskRuns |
| `cluster_inventory` | Combined correlator | Desired state, actual state, latest run, and drift |
| `workflow_view` | Workflow correlator | Operator-facing PipelineRun view |
| `cluster_state_correlation` | Cluster-state correlator | Per-cluster evidence links and missing evidence |

## Cluster Inventory Fields

| Field | Source |
| --- | --- |
| `cluster_name` | Git or OpenShift |
| `definition_status` | Git: `active`, `archived`, or `conflict` |
| `definition_statuses` | Every matching Git status |
| `definition_conflict` | Whether multiple Git definitions match one cluster |
| `definition_kind` | Matching Git item kind, or `null` when no definition or a conflict exists |
| `tier` | Desired or actual state |
| `cloud_provider` | Desired or actual state |
| `region` | Desired or actual state |
| `desired_state` | Git |
| `actual_state` | OpenShift |
| `provisioning_status` | OpenShift `Provisioned` condition |
| `latest_pipeline_run` | Tekton |
| `drift_indicator` | Derived |

Drift values:

| Value | Meaning |
| --- | --- |
| `none` | Desired and actual state match |
| `detected` | Desired and actual state differ |
| `incomplete-definition` | Active Git definition is missing required files and has an OpenShift resource, so desired and actual state cannot be compared |
| `invalid-definition` | Active Git definition cannot be interpreted and has an OpenShift resource |
| `missing-git` | OpenShift resource has no Git definition |
| `missing-openshift` | Active Git definition has no OpenShift resource |
| `archived` | Archived definition has no OpenShift resource |
| `archived-with-openshift-resources` | Archived definition still has an OpenShift resource |
| `definition-conflict` | Active and archived Git definitions both match one cluster |

## PipelineRun Item Fields

| Field | Source |
| --- | --- |
| `pipeline_run_name` | Tekton `metadata.name` |
| `pipeline_run_id` | Tekton `metadata.uid` |
| `namespace` | Tekton `metadata.namespace` |
| `cluster_name` | Tekton `cluster-name` parameter |
| `status` | Tekton `Succeeded` condition |
| `start_time` and `completion_time` | Tekton status |
| `conditions` | Tekton status conditions |
| `task_runs` and `failed_task` | Summarized TaskRuns |

## Cluster State Correlation Fields

| Field | Meaning |
| --- | --- |
| `cluster_name` | Requested cluster name |
| `namespace` | OpenShift namespace |
| `git_evidence_id` | Single definition item, or `null` during a conflict |
| `git_evidence_ids` | Every matching Git definition item |
| `definition_conflict` | Whether more than one Git definition matched |
| `openshift_evidence_id` | Actual-state item |
| `pipeline_run_evidence_ids` | Selected PipelineRun item or items |
| `missing_evidence` | Reasons a diagnosis cannot proceed |

PipelineRun selection defaults to the matching run with the latest `start_time`. The `diagnose state --pipeline-run` flag can select by PipelineRun name or UID.

## Workflow View Fields

| Field | Source |
| --- | --- |
| `pipeline_run_name` | Tekton |
| `pipeline_name` | Tekton |
| `status` | Tekton |
| `start_time` | Tekton |
| `completion_time` | Tekton |
| `failed_task` | Tekton |
| `related_cluster` | Tekton `cluster-name` parameter, then namespace correlation |
| `next_diagnostic_step` | Deterministic workflow logic |

## CWA Snapshot

The CWA assembler emits all required slots:

| Slot | Aura content |
| --- | --- |
| `governance.instructions` | Read-only policy |
| `governance.capabilities` | Empty Phase 1 adapter allow list |
| `governance.output_contract` | Command output schema |
| `state.task` | Command type, status, and input |
| `state.user` | Operator ID and roles |
| `evidence.tool_results` | Snapshot metadata and items |
| `evidence.knowledge` | Empty Phase 1 knowledge list |
| `interaction.query` | Operator query |
| `interaction.history` | Current command history |

`CWASnapshot` validates:

- All nine required slots are present.
- Slot names are unique.
- Digests match canonical JSON.
- Timestamps are timezone-aware.

## CWA Trace

`CWATrace` links:

- Command ID.
- CWA snapshot ID and digest.
- Evidence snapshot ID and digest.
- Audit event ID.
- Assembler version and assembly timestamp.
- Optional model provider, model name, and response digest.

The trace is stored even when diagnosis is refused.

## Diagnosis Response

`DiagnosisResponse` uses strict JSON with no extra fields:

| Field | Constraint |
| --- | --- |
| `summary` | Non-blank string |
| `facts` | List of evidence-linked facts |
| `inferences` | List of confidence-bounded inferences |
| `missing_evidence` | List of non-blank strings |
| `recommended_inspection` | Non-blank read-only next step |
| `confidence` | Number from `0` through `1` |
| `evidence_ids` | Unique UUID references |

Every fact's evidence ID must appear in `evidence_ids`.

Cluster-state diagnosis rules:

- Tekton `cluster-name` is the primary workflow correlation key.
- Conflicting Git definitions, incomplete definitions, invalid definitions, and a missing OpenShift `Provisioned` condition cause refusal.
- A non-provisioned cluster receives a summary that says it is not provisioned, a fact citing its `Provisioned` condition, and a read-only inspection recommendation.
- A failed PipelineRun may be linked to non-provisioning as an inference, not as a fact.
- The summary names the PipelineRun it describes. When several runs are selected, a `Failed` run takes precedence, then a run that has not completed, then the first run.
- A PipelineRun whose `Succeeded` condition is neither `True` nor `False` is reported as not completed, never as succeeded, with an inference that the cluster state may still change.

| PipelineRun status | Summary outcome | Confidence |
| --- | --- | --- |
| `Failed` | `failed` | `0.8` |
| `Succeeded` | `succeeded` | `0.9` |
| Any other value | `has not completed` | `0.6` |

## Diagnosis Record and Answer

`DiagnosisRecord` contains:

- Diagnosis ID.
- Command ID.
- CWA snapshot ID and digest.
- Provider and model names.
- Fallback indicator.
- Diagnosis timestamp.
- Response and response digest.

`DiagnosisAnswer` adds:

- Evidence snapshot ID.
- Evidence snapshot digest.
- CWA trace ID.

## Audit Events

Audit events are immutable and reference the previous event where applicable:

| Type | Data |
| --- | --- |
| `command_received` | Command type and input |
| `evidence_collected` | Snapshot ID, digest, collector, source, source version, persistence flag |
| `context_assembled` | CWA snapshot and trace IDs, evidence digest, assembler version |
| `model_called` | Diagnosis ID, response digest, provider, model, fallback flag, trace ID |
| `command_failed` | Terminal failure reason |
| `command_refused` | Terminal refusal reason |
