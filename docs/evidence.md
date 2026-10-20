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
| `source_version` | Git commit, joined resource versions, or supplied version |
| `items` | Typed evidence items |
| `redactions` | Fields removed before storage |
| `snapshot_digest` | SHA-256 digest of canonical snapshot JSON |

## Evidence Item Kinds

| Kind | Producer | Meaning |
| --- | --- | --- |
| `cluster_definition` | Git | Complete active or archived desired-state definition |
| `cluster_definition_incomplete` | Git | Definition directory with missing required files |
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
| `definition_status` | Git: `active` or `archived` |
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
| `missing-git` | OpenShift resource has no Git definition |
| `missing-openshift` | Active Git definition has no OpenShift resource |
| `archived` | Archived definition has no OpenShift resource |
| `archived-with-openshift-resources` | Archived definition still has an OpenShift resource |

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
| `evidence_collected` | Snapshot ID, digest, collector, source, source version, persistence flag |
| `context_assembled` | CWA snapshot and trace IDs, evidence digest, assembler version |
| `model_called` | Diagnosis ID, response digest, provider, model, fallback flag, trace ID |
