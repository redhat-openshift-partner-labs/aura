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
| `redactions` | Fields removed before storage; PipelineRun parameters use `pipeline_run[NAMESPACE/NAME].params.PARAM`, and combined snapshots keep the redactions of the items they include |
| `snapshot_digest` | SHA-256 digest of canonical snapshot JSON |

## Evidence Item Kinds

| Kind | Producer | Meaning |
| --- | --- | --- |
| `cluster_definition` | Git | Complete active or archived desired-state definition. `patch_platform` is the cloud named by the first `/spec/platform/<cloud>` ClusterDeployment patch, and `metadata_text` is the raw `metadata.yaml` text |
| `cluster_definition_incomplete` | Git | Definition directory with missing required files; `cluster_name` is the directory name. `patch_platform`, `crossplane_template`, and `tier` record what a present, readable ClusterDeployment patch, Crossplane kustomization, and ManagedCluster patch declare, or `null`. When `metadata.yaml` is the only missing file, the other files are validated as for a complete definition and `remaining_files_error` records the problem, or `null` |
| `cluster_definition_invalid` | Git | Definition with all required files whose YAML or required patch paths cannot be interpreted; `cluster_name` is the directory name and `error` names the file and problem |
| `deprovision_sentinel` | Git | File under `deprovision/`, other than `README.md`; `cluster_name` strips a `-archive` suffix and `archive_suffix` records whether it was present |
| `cluster_actual_state` | OpenShift | Complete observed cluster state |
| `cluster_actual_state_incomplete` | OpenShift | Cluster resource with missing status data |
| `pipeline_run` | Tekton | PipelineRun and summarized TaskRuns |
| `pipeline_definition` | Tekton Pipeline | Parameter definitions of one Pipeline in `openshift-pipelines` |
| `cluster_inventory` | Combined correlator | Desired state, actual state, latest run, and drift |
| `workflow_view` | Workflow correlator | Operator-facing PipelineRun view |
| `cluster_state_correlation` | Cluster-state correlator | Per-cluster evidence links and missing evidence |
| `proposal_staleness` | Proposal staleness collector | Git staleness of one stored proposal |

## Cluster Inventory Fields

| Field | Source |
| --- | --- |
| `cluster_name` | Git or OpenShift |
| `definition_status` | Git: `active`, `archived`, or `conflict` |
| `definition_statuses` | Every matching Git status |
| `definition_conflict` | Whether multiple Git definitions match one cluster |
| `definition_kind` | Matching Git item kind, or `null` when no definition or a conflict exists |
| `deprovision_status` | Deprovision state derived from sentinels, definitions, and OpenShift, or `null` without a sentinel |
| `deprovision_sentinels` | Paths of matching `deprovision/` sentinels |
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
| `detected` | Desired and actual state differ in a known field |
| `incomplete-metadata` | Known fields match, but `metadata.yaml` has no `platform`, so the desired cloud provider is unknown |
| `incomplete-definition` | Active Git definition is missing required files and has an OpenShift resource, so desired and actual state cannot be compared |
| `invalid-definition` | Active Git definition cannot be interpreted and has an OpenShift resource |
| `missing-git` | OpenShift resource has no Git definition |
| `missing-openshift` | Active Git definition has no OpenShift resource |
| `archived` | Archived definition has no OpenShift resource |
| `archived-with-openshift-resources` | Archived definition still has an OpenShift resource |
| `definition-conflict` | Active and archived Git definitions both match one cluster |
| `deprovision-requested` | An active definition has a deprovision sentinel; see `deprovision_status` |

Deprovision status values:

| Value | Meaning |
| --- | --- |
| `pending` | Active definition with a sentinel and OpenShift resources still present |
| `archive-pending` | Active definition with a `-archive` sentinel and no OpenShift resources; the archive Action has not moved it yet |
| `stranded` | Active definition with only bare sentinels and no OpenShift resources; the archive Action requires `deprovision/<id>-archive` and cannot archive it |
| `leftover` | Every matching definition is archived, but a sentinel remains |
| `orphaned` | A sentinel exists without any matching definition |

## PipelineRun Item Fields

| Field | Source |
| --- | --- |
| `pipeline_run_name` | Tekton `metadata.name` |
| `pipeline_run_id` | Tekton `metadata.uid` |
| `namespace` | Tekton `metadata.namespace` |
| `cluster_name` | Tekton `cluster-name` parameter |
| `params` | Every `spec.params` entry as a name-to-value map, with secret-named values redacted |
| `workspaces` | Workspace bindings as `name`, `source`, `reference`, `sub_path`, `claim_spec`, and `unsupported_fields`, which names binding fields the summary does not carry, such as a Secret's `defaultMode`, ignoring Go zero values such as `creationTimestamp: null` and `status: {}`; Secrets and ConfigMaps appear by name only |
| `run_template` | `service_account_name` from `taskRunTemplate.serviceAccountName`, `pod_template` with only `securityContext`, `imagePullSecrets`, `nodeSelector`, and `tolerations`, and `unsupported_fields`, which names any other `spec`, `taskRunTemplate`, or `podTemplate` field with a non-zero value, without the value. `spec.timeouts` and `spec.status` are neither copied nor unsupported. |
| `status` | Tekton `Succeeded` condition: `Succeeded` when `True`, `Failed` when `False`, otherwise `Unknown` |
| `start_time` and `completion_time` | Tekton status |
| `conditions` | Tekton status conditions |
| `task_runs` and `failed_task` | Summarized TaskRuns |

## Pipeline Definition Fields

| Field | Source |
| --- | --- |
| `namespace` and `pipeline_name` | Pipeline `metadata` |
| `params` | Each well-formed `spec.params` entry as `name`, `type` (`string` when absent), and `has_default`. Default values are never stored, because validation needs only whether a default exists and a default can hold secret material under any name. A `null` default counts as no default, as in Tekton. |
| `required_params` | Names of parameters without a default, in declared order |
| `malformed_params` | Entries that are not mappings, lack a non-empty name, have a type other than `string`, `array`, or `object`, or repeat an earlier name, plus one when `spec` or `spec.params` has the wrong shape. Such entries are counted, never silently dropped. |

A requested Pipeline that is absent, or a Pipeline without a name, raises `PipelineDefinitionError` rather than producing an empty snapshot. Object parameter `properties` are not recorded; every `fleet` Pipeline uses string parameters.

## Cluster State Correlation Fields

| Field | Meaning |
| --- | --- |
| `cluster_name` | Requested cluster name |
| `namespace` | OpenShift namespace |
| `git_evidence_id` | Single matching Git item, complete, incomplete, or invalid, or `null` during a conflict |
| `git_evidence_ids` | Every matching Git item |
| `definition_conflict` | Whether more than one Git definition matched |
| `openshift_evidence_id` | Actual-state item |
| `pipeline_run_evidence_ids` | Selected PipelineRun item or items |
| `deprovision_status` | Same values as combined inventory, or `null` without a sentinel |
| `deprovision_sentinel_evidence_ids` | Matching `deprovision_sentinel` items |
| `unfinished_pipeline_runs` | Names of matching runs of the selected Pipeline, or of any Pipeline when none is selected, that have not succeeded or failed, including pending runs with no start time |
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

The trace is stored even when diagnosis or a proposal is refused. For a stored proposal, the trace records provider `aura`, the generator name as model name, and the `proposal_digest` as response digest.

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
- When `deprovision_status` is `pending`, `archive-pending`, or `stranded`, missing OpenShift resources, an incomplete or invalid definition, and a missing PipelineRun do not cause refusal, because none of them affects the deprovision explanation. Tekton prunes old PipelineRuns, so a deprovision answer without a run says `no PipelineRun is retained for it` and has confidence `0.6`. Conflicting definitions and an absent `--pipeline-run` selection still cause refusal. Each sentinel is cited as a fact, and the summary describes the deprovision state:

| Deprovision status | Summary | Inference |
| --- | --- | --- |
| `pending` | Pending deprovision request, with the PipelineRun outcome | None beyond PipelineRun inferences |
| `archive-pending` | No OpenShift resources; awaiting archiving through the `-archive` sentinel | The archive Action has not moved the definition yet, confidence `0.6` |
| `stranded` | No OpenShift resources and only a bare sentinel | The archive Action requires `deprovision/<id>-archive`, confidence `0.8` |

A `leftover` or `orphaned` sentinel does not relax refusal.
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

## Proposal

A `Proposal` is an immutable, evidence-linked change that Phase 2 never executes. Its fields follow the [Phase 2 proposal contract](plans/05-phase-2-proposals.md#proposal-contract), plus `created_at` and `status`, which is always `proposal_ready` in Phase 2. `proposal_digest` is a SHA-256 digest over canonical JSON of every other field; a changed field under an old digest fails validation.

The contract enforces these invariants regardless of which generator produced the proposal:

| Rule | Scaffold | Deprovision | Cluster edit | Post-provision rerun |
| --- | --- | --- | --- | --- |
| Artifact | `patch` | `patch` | `patch` | `pipeline_run` |
| `base_commit` | Required | Required | Required | Absent |
| Patch scope | Adds only files under `provision/<id>/` | Adds only the empty regular file (mode `100644`) `deprovision/<id>-archive` | Adds `provision/<id>/metadata.yaml`, or appends to it in one hunk that starts at line 1 and covers the whole file; only keys the file lacks, from the four `fleet` keys, and the result declares at least `platform`, `aws-credentials-mode`, and `htpasswd-provider-name` | None |
| Commit message | Names the cluster; no `[skip-provision]` | Names the cluster; no `[skip-deprovision]` | Names the cluster; contains `[skip-provision]` when a file is added | None |
| `trigger_effect` | `pre-provision`, `provision`, `post-provision` | `deprovision`, `archive-cluster` | None | `post-provision` |
| `reversibility` | `reversible_before_merge` | `reversible_before_merge` | `reversible` | `irreversible` |
| Other | Requires `scaffold_commit` | | | `PipelineRun` for the `post-provision` Pipeline with only `apiVersion`, `kind`, `metadata` (`generateName`, `namespace`, and `annotations` limited to `aura.openshiftpartnerlabs.com/proposal-id` and `aura.openshiftpartnerlabs.com/source-pipeline-run`), and `spec` (`pipelineRef` with only `name`, `params`, `workspaces`, `taskRunTemplate` with only `serviceAccountName` and a `podTemplate` of `securityContext`, `imagePullSecrets`, `nodeSelector`, and `tolerations`); `generateName` `post-provision-<id>-` in `openshift-pipelines`; unique parameter names with `cluster-name` equal to the target; each workspace with exactly one of `volumeClaimTemplate` (`spec`), `persistentVolumeClaim` (`claimName`), `secret` (a non-empty `secretName` and `items` of `key`, `path`, and `mode`), `configMap` (`name`), or `emptyDir`, and a relative `subPath` without `..`; no secret-named parameters or fields outside those Secret bindings |

Patches must be Git-format diffs. Every proposal names exactly one cluster, and every patched path must belong to it.

`GitProposalStaleness` reports a Git proposal as stale when its `base_commit` is not an ancestor of `fleet-clusters` `HEAD`, or when any commit since `base_commit` changed `provision/<id>`, `archive/<id>`, `deprovision/<id>`, or `deprovision/<id>-archive`. Changes to other clusters do not make it stale. Post-provision reruns have no base commit; their evidence is rechecked when they are generated or executed.

### Deprovision Proposal

`DeprovisionProposalGenerator` (`deprovision-sentinel-deterministic`, version `0.1.0`) consumes a cluster-state CWA snapshot. Every text field comes from a fixed template; no model writes any part of it.

| Field | Value |
| --- | --- |
| `proposal_id` | UUIDv5 of the command ID and CWA snapshot ID |
| `base_commit` | Pinned `fleet-clusters` revision from the evidence `git=` source version |
| `patch` | Adds the empty file `deprovision/<id>-archive` |
| `commit_message` | Names the cluster, says the commit is standalone and adds only the sentinel, and ends with `Aura-Proposal: <proposal_id>` |
| `risks` | Cluster destruction on merge, the one-cluster-per-push trigger rule, and a failed pipeline leaving `provision/<id>` in place |
| `rollback` | Discard before merge; none once the pipeline starts |

Refusal reasons, all reported together:

| Unmet precondition | Reason |
| --- | --- |
| No correlation item | `no cluster state correlation in evidence` |
| No Git definition | `no Git cluster definition for <id>` |
| Active and archived definitions | `conflicting Git cluster definitions for <id>` |
| Definition outside `provision/` | `cluster <id> is defined at <path>, not provision/<id>` |
| Existing sentinel | `deprovision sentinel already exists: <path>` |
| No OpenShift resources | `no OpenShift resources for cluster <id>` |
| No pinned revision | `no pinned Git base commit in evidence` |

### Cluster-Edit Proposal

`ClusterEditProposalGenerator` (`cluster-edit-deterministic`, version `0.1.0`) consumes a cluster-state CWA snapshot whose PipelineRun is the latest `post-provision` run. It leaves `metadata.yaml` with every key `fleet` reads for the cluster's tier, adding only the keys that are missing; existing keys and lines never change. `openshift-ai-version` applies only to tier `ai`, because `apply-ai-workloads`, its only reader, runs only for that tier. The tier is the Git ManagedCluster patch `/metadata/labels/tier`, which must match the OpenShift ManagedCluster `tier` label when that label exists. The contract carries no tier, so it requires only the three tier-independent keys and the generator enforces the rest.

| Key | Value, in order of precedence |
| --- | --- |
| `platform` | The ClusterDeployment patch cloud, which must match the OpenShift ClusterDeployment platform. A valid definition always has it; without it the proposal is refused. `fleet` `9464f0b` writes `platform` as a task result but no task reads it, so this key records the cloud without changing pipeline behavior. |
| `aws-credentials-mode` | `legacy`, the value the pipeline uses when the key is absent |
| `htpasswd-provider-name` | The latest `post-provision` run's parameter; else `htpasswd` |
| `openshift-ai-version` | Tier `ai` only: the latest `post-provision` run's parameter; else `v3`. For another tier it is never added, and an existing value is not compared with the run |

Defaults are those of `read-cluster-metadata` at `fleet` commit `9464f0b`. A new file lists the keys in the order above. Appended keys follow the existing text, after a newline is added to an unterminated last line, and the patch carries the whole existing file as context so the contract can check the result without the repository. The `summary` names each value's source.

Refusal reasons, reported together with the shared live-cluster reasons (no definition, conflicting definitions, a definition outside `provision/`, an existing deprovision sentinel, no OpenShift resources, no pinned base commit):

| Unmet precondition | Reason |
| --- | --- |
| Invalid definition, or invalid other files when only `metadata.yaml` is missing | `definition for <id> is invalid: <error>` |
| `metadata.yaml` uses CR line endings | `metadata.yaml for <id> has CR line endings` |
| No ClusterDeployment platform patch | `no ClusterDeployment platform evidence for <id>` |
| Other required files missing | `definition for <id> is missing files other than metadata.yaml: [...]` |
| ClusterDeployment patch and OpenShift disagree | `platform evidence conflicts for <id>: ClusterDeployment patch <a>, OpenShift <b>` |
| Existing `platform` disagrees with evidence | `metadata.yaml declares platform <a> but evidence shows <b>` |
| Scoped Crossplane template without the key | `cluster <id> uses scoped AWS credentials but metadata.yaml does not declare aws-credentials-mode` |
| Existing credential mode disagrees with the template | `metadata.yaml declares aws-credentials-mode <mode> but the Crossplane template is <template>` |
| Git and OpenShift tiers disagree | `tier evidence conflicts for <id>: ManagedCluster patch <a>, OpenShift <b>` |
| Existing key disagrees with the run | `metadata.yaml declares <key> <a> but PipelineRun <name> used <b>` |
| Redacted run parameter | `PipelineRun <name> parameter <key> is redacted` |
| Nothing missing | `metadata.yaml for <id> already declares every key tier <tier> needs` |
| Appending would change the parsed file, such as after a flow mapping | `metadata.yaml for <id> cannot be extended by appending keys` |

Scoped credentials are recognized only by the `crossplane-scoped` Crossplane template, which the scaffold writes for every scoped AWS cluster; the scaffold's other scoped markers are not checked.

`metadata_text` is evidence, and redaction works on field names, so a secret value typed into `metadata.yaml` would not be redacted. The file holds only the four non-secret keys today.

### Post-Provision Rerun Proposal

`PostProvisionRerunGenerator` (`post-provision-rerun-deterministic`, version `0.1.0`) copies a finished `post-provision` run into an unapplied PipelineRun manifest: its parameters in order, its workspaces rebuilt from the evidence summary, its run template, and annotations `aura.openshiftpartnerlabs.com/proposal-id` and `aura.openshiftpartnerlabs.com/source-pipeline-run`. Parameters the source run did not set take the Pipeline defaults when the run is created.

Refusal reasons, reported together with the shared live-cluster reasons (no definition, conflicting definitions, a definition outside `provision/`, an existing deprovision sentinel, no OpenShift resources):

| Unmet precondition | Reason |
| --- | --- |
| Cluster not provisioned | `cluster <id> is not Provisioned` |
| No retained run | `no post-provision PipelineRun for cluster <id>` |
| Named run absent or not `post-provision` | `Selected Tekton PipelineRun <name>` |
| Any `post-provision` run for the cluster unfinished, including the source or a pending run | `post-provision runs for cluster <id> have not finished: [...]` |
| Source run outside `openshift-pipelines` | `PipelineRun <name> is in namespace <ns>, not openshift-pipelines` |
| Source run for another cluster, matched by namespace | `PipelineRun <name> is for cluster <other>, not <id>` |
| Malformed Pipeline definition | `the post-provision Pipeline has <n> malformed parameters` |
| Missing required parameters | `PipelineRun <name> lacks required parameters: [...]` |
| Parameters the Pipeline does not declare | `PipelineRun <name> sets parameters the post-provision Pipeline does not declare: [...]` |
| Redacted parameter | `PipelineRun <name> parameter <param> is redacted` |
| `metadata.yaml` disagrees | `PipelineRun <name> would use <key> <value> but metadata.yaml declares <value>` (or `defaults to`) |
| `metadata.yaml` cannot be checked | `cannot check metadata.yaml for <id>: its definition is missing [...]` (or `is invalid`) |
| Fields a rerun cannot copy | `PipelineRun <name> has fields a rerun cannot copy: [...]` |
| Workspace source a rerun cannot rebuild (`csi`, `projected`, unknown) | `PipelineRun <name> workspace <ws> uses source <source>, which a rerun cannot copy` |

A generated manifest that the contract still rejects, such as a declared parameter whose value contains a secret-named key, fails the command with `generated proposal is invalid:` rather than refusing it.

The `metadata.yaml` check compares `htpasswd-provider-name`, and `openshift-ai-version` only when the source run's `tier` parameter is `ai`, the condition under which `apply-ai-workloads` reads it. The risks name the keys compared. When the source run did not set a key, the rerun's value is assumed to be the `fleet` `9464f0b` Pipeline default, because Pipeline definition evidence stores no default values.

### Proposal Staleness Fields

| Field | Meaning |
| --- | --- |
| `proposal_id`, `proposal_type`, `cluster_name`, `proposal_digest` | Copied from the verified stored proposal |
| `base_commit` | Proposal base commit; `null` for post-provision reruns |
| `head` | Pinned `fleet-clusters` `HEAD`, also the snapshot `source_version` |
| `git_checked` | `false` when the proposal has no base commit, so Git staleness does not apply |
| `stale` | Whether `HEAD` moved past `base_commit` and touched the cluster, or `base_commit` is not an ancestor of `HEAD` |
| `reasons` | Changed cluster paths or the history problem |

## Audit Events

Audit events are immutable and reference the previous event where applicable:

| Type | Data |
| --- | --- |
| `command_received` | Command type and input |
| `evidence_collected` | Snapshot ID, digest, collector, source, source version, persistence flag |
| `context_assembled` | CWA snapshot and trace IDs, evidence digest, assembler version |
| `model_called` | Diagnosis ID, response digest, provider, model, fallback flag, trace ID |
| `proposal_created` | Proposal ID, digest, type, cluster, base commit, CWA snapshot and trace IDs, generator and version |
| `command_failed` | Terminal failure reason |
| `command_refused` | Terminal refusal reason |
