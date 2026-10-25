# Validation

## Automated Tests

Run:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
```

Expected current result:

```text
633 passed
```

Run the CI workflow locally with `act` before reporting work ready to publish; see [Run the CI Workflow Locally](development.md#9-run-the-ci-workflow-locally). The latest run, at `01df26b`, passed with 633 tests on Python 3.13.17 in `linux/arm64`.

Run diff hygiene:

```bash
git diff --check
```

Run the structural quality gate:

```bash
ripwire . --quality-delta=adb2c2b..HEAD
```

Expected current result: zero gating findings. The committed range is the quality baseline through the current changes.

Confirmed false positives are acknowledged in `.ripwire_quality_acks`, never by editing the baseline. Acknowledge only the specific findings, with a reason:

```bash
ripwire . --quality-delta=adb2c2b..HEAD \
  --quality-ack="REASON" \
  --ack-only=SUBSTRING
```

The current ledger acknowledges the store exception classes, which `JsonRecordStore` references as class attributes and `cli.py` catches by name, and the evidence route properties and collector selectors, which `cli.py` reads as attributes or references as `_EVIDENCE_ROUTES` values; ripwire counts only direct construction or calls as use.

The test classes every change must include are defined in [Development Workflow](development.md). The test suite covers:

- Command and state contracts.
- Authorization.
- Git, OpenShift, and Tekton collectors, including incomplete and invalid Git definitions.
- Cluster and workflow correlation.
- Cluster-state diagnosis, including failed, succeeded, and not-completed PipelineRuns.
- Refusal when evidence is missing.
- CWA assembly and digests.
- Immutable JSON stores, record write failures, and unreadable command records.
- CLI argument combinations and output.
- Proposal contract invariants, the proposal store, and Git proposal staleness.
- Deprovision proposal generation over real temporary Git trees, including `git apply --check` against the base commit and every refusal reason.
- `propose deprovision` authorization, stored records, audit chain, refusal, and argument validation.
- Proposal staleness collection against one pinned `HEAD`, requested-ID selection, and rejection of missing, misnamed, or tampered records.
- `proposal status` authorization for `viewer`, `proposer`, and `admin`, output, and failure records, including missing and unreadable stores.
- Write order: a failed trace, audit, or proposal write leaves no proposal file, and `command_failed` links to the last stored audit event.
- Pipeline definition collection: parameters, required parameters, `null` defaults, no stored default values, malformed and duplicate entries, unknown types, missing or unnamed Pipelines, and a forbidden `oc` response.
- Post-provision rerun generation over real Git, Tekton, and Pipeline definition collectors: copied parameters, workspaces, and run template; named and latest source runs; every refusal reason, including a `metadata.yaml` conflict; and the tightened rerun contract.
- Cluster-edit generation over real temporary Git trees: added and appended `metadata.yaml` files that apply with `git apply --check`, preserve existing bytes, and leave a complete definition; values from runs and defaults; every refusal reason.
- Deprovision contract rejection of non-empty or non-regular sentinel files.
- The failure-injection matrix: every command's documented write order, a failure of each write in turn before writing and after linking, and each route's read errors, with invariants I1–I4 checked after every run.
- Fixture validity and secret redaction, including field spelling variants.

## Local Fixture

This procedure is pinned to the `fleet-clusters` commit below; the fixture cluster has since been deprovisioned, so current `HEAD` no longer matches these results. The automated tests use a static copy of this fixture.

Fixture cluster:

```text
provision/<cluster-name>
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

- `provision/<cluster-name>`.
- `status: active`.
- `cluster_name: <cluster-name>`.
- Archived definitions with `status: archived`.
- 26 definitions at the validated commit: 12 `cluster_definition`, 14 `cluster_definition_incomplete`, and no `cluster_definition_invalid`.
- Every incomplete definition has a `cluster_name` equal to its directory name.
- 7 `deprovision_sentinel` items, all with `archive_suffix: false`.

## Current Git Inventory

Run the Git inventory command above against current `fleet-clusters` `HEAD` with a new idempotency key, saving its output to `inventory.json`. Count item kinds and list any definition that is not complete or has no `cloud_provider`:

```bash
jq -r '.items[].kind' inventory.json | sort | uniq -c
jq -r '.items[] | select(.kind | startswith("cluster_definition")) | select(.kind != "cluster_definition" or .data.cloud_provider == null) | [.kind, .data.path] | @tsv' inventory.json
```

Latest result, recorded on 2026-10-25 against `fleet-clusters` commit `a8a6bf0`, after the operator's `metadata.yaml` backfill in `3cf829d` and a ClusterDeployment patch fix in `a8a6bf0`:

| Item kind | Before backfill (`492bc8e`) | After backfill (`3cf829d`) | Current (`a8a6bf0`) |
| --- | --- | --- | --- |
| `cluster_definition` | 12 | 26 | 27 |
| `cluster_definition_incomplete` | 14 | 0 | 0 |
| `cluster_definition_invalid` | 0 | 1 | 0 |
| Definitions without `cloud_provider` | 11 | 0 | 0 |
| `deprovision_sentinel` | 7 | 7 | 7 |

The invalid definition at `3cf829d` was archived; its ClusterDeployment patch lacked `/spec/provisioning/imageSetRef/name`. Before the backfill, its missing `metadata.yaml` masked that defect, because Aura reports missing files before parsing patches. Every committed `metadata.yaml` declares all four `fleet` keys.

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

Latest validation observed. The previous column was recorded at Aura commit `655a0ac` against `fleet-clusters` `492bc8e`. The current column was run on 2026-10-25 with the `aura:aura-readonly` kubeconfig, at Aura commit `f782a7d` against `fleet-clusters` `a8a6bf0`, after the [metadata backfill](#current-git-inventory); `workflow inspect` was not re-run.

| Metric | Previous | Current |
| --- | --- | --- |
| OpenShift cluster states | 13 | 12 |
| PipelineRuns | 34 | 44 |
| Combined inventory records | 25 | 26 |
| Combined inventory evidence items | 105 | 116 |
| `missing-git` or `detected` drift | 0 | 0 |
| `missing-openshift` drift | 0 | 1 |
| `incomplete-definition` drift | 5 | 0 |
| `incomplete-metadata` drift | 6 | 0 |
| `none` drift | Not recorded | 11 |
| `archived` drift | Not recorded | 7 |
| `definition-conflict` drift | Not recorded | 1 |
| `deprovision-requested` drift | 6, all `deprovision_status: stranded` | 6, all `deprovision_status: stranded` |
| `leftover` deprovision status | 1 | 1 |
| Combined snapshot redactions | None; no live parameter name contains a non-allowlisted secret term | None |
| Record files | Not recorded | 1 command, 1 snapshot, 3 audit, 1 CWA snapshot, 1 CWA trace; no `.tmp` files |
| Workflow evidence items | 114 | Not re-run |
| Workflow views | 34 | Not re-run |
| Correlated workflow views | 34 | Not re-run |
| Uncorrelated workflow views | 0 | Not re-run |

The backfill cleared every `incomplete-definition` and `incomplete-metadata` record, and no backfilled `platform` disagrees with OpenShift. The new `missing-openshift` record is an active `provision/<cluster-name>` definition with no deprovision sentinel, no ClusterDeployment or ManagedCluster, and no retained PipelineRun; it had OpenShift resources in the previous run. Aura cannot tell from read-only evidence why they were removed.

## Live Diagnosis Validation

Validate a cluster that has a definition, OpenShift resources, and a retained PipelineRun:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura diagnose state \
  --cluster <cluster> \
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

The cluster results below are historical, recorded before the fixture cluster was deprovisioned; current cluster states differ. Run the same command with new idempotency keys against clusters in comparable states to validate refusals:

| Cluster | Expected result |
| --- | --- |
| A cluster with a succeeded PipelineRun | Exit code `0`; summary names a succeeded PipelineRun |
| A cluster with incomplete definition files | Exit code `2`; refused with `Git cluster definition files` and `Tekton PipelineRun status` |
| A cluster with no OpenShift resources or retained run | Exit code `2`; refused with `OpenShift actual state` and `Tekton PipelineRun status` |
| A cluster with conflicting definitions | Exit code `2`; refused with `Conflicting Git cluster definitions` |
| A stranded cluster with a retained deprovision run | Exit code `0`; stranded deprovision summary citing its retained deprovision PipelineRun, confidence `0.9` |
| A stranded cluster with no retained run | Exit code `0`; stranded deprovision summary stating `no PipelineRun is retained for it`, confidence `0.6` |
| A cluster with a leftover sentinel | Exit code `2`; leftover sentinel, refused with `Git cluster definition files` and `OpenShift actual state` |

Historical validation observed:

| Check | Result |
| --- | --- |
| Fixture diagnosis | Exit code `0`; summary names PipelineRun `post-provision-<cluster-name>-<suffix>` as succeeded with confidence `0.9` |
| Evidence items | Snapshot holds 1 definition, 1 actual state, 1 PipelineRun, and 1 correlation; the response cites the first three |
| Provenance | Evidence, CWA snapshot, CWA trace, diagnosis, and audit records linked by matching IDs and digests |
| Refusals | All four refusal clusters exited `2` with the expected missing evidence; no diagnosis record was written for them |
| Stranded deprovisions | All six stranded clusters exited `0`: two cite a retained deprovision PipelineRun, four state that no PipelineRun is retained |

## Live Proposal Validation

Choose a cluster that has OpenShift resources and no `deprovision/` sentinel. Generating a proposal reads only; it writes nothing to `fleet-clusters` or the hub.

```bash
export CLUSTER=<live-cluster> OUT=./local/propose-validation
mkdir -p "$OUT"
```

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura propose deprovision \
  --cluster "$CLUSTER" \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$AURA_KUBECONFIG" \
  --snapshot-dir "$OUT/snapshots" \
  --audit-dir "$OUT/audit" \
  --cwa-dir "$OUT/cwa" \
  --proposal-dir "$OUT/proposals" \
  --role proposer \
  --operator operator@example.com \
  --idempotency-key propose-live-001 \
  --command-dir "$OUT/commands" > "$OUT/proposal.json"
```

Check the proposal against `fleet-clusters` without changing it:

```bash
jq -r .patch "$OUT/proposal.json" | git -C ../fleet-clusters apply --check -v -
```

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync python -c 'import sys; from pathlib import Path; from aura.proposals import Proposal, patch_changes; from aura.collectors.git_staleness import GitProposalStaleness; p = Proposal.model_validate_json(Path(sys.argv[1]).read_text()); print(patch_changes(p.patch), [str(e) for e in p.trigger_effect]); print(GitProposalStaleness(Path(sys.argv[2])).check(p))' "$OUT/proposal.json" ../fleet-clusters
```

Then rerun the command with new idempotency keys for a deprovisioned cluster (`--cluster <deprovisioned-cluster>`), and with `--role viewer`.

| Check | Expected result |
| --- | --- |
| Success | Exit code `0`; one proposal file; `base_commit` equals `fleet-clusters` `HEAD` |
| Patch | `git apply --check` passes; the only change adds `deprovision/<id>-archive` |
| Contract | Proposal validates with its digest; trigger effects `deprovision`, `archive-cluster`; not stale |
| Commit message | Names the cluster, states it is standalone, ends with `Aura-Proposal: <id>` |
| CWA trace | Provider `aura`, model `deprovision-sentinel-deterministic`, response digest equal to `proposal_digest` |
| Audit chain | `command_received`, `evidence_collected`, `context_assembled`, `proposal_created`, each linked to the previous event |
| Deprovisioned cluster | Exit code `2`; `proposal refused:` including `no OpenShift resources for cluster <id>`; no proposal stored |
| `viewer` role | Exit code `2`; `operator is not authorized to propose changes`; no evidence collected |
| Command records | `propose.patch` with `succeeded`, `refused`, and `failed` |
| `fleet-clusters` | `git status --porcelain` is empty |

Latest validation, run by the operator on 2026-10-23 against Aura commit `c5abafb` with one live cluster for the proposal and a deprovisioned cluster for refusal: every check above produced the expected result.

### Cluster-Edit Proposals

Choose a live cluster whose definition lacks `metadata.yaml` or some of its keys, with no deprovision sentinel, and run [`propose cluster-edit`](cli.md#propose-cluster-edit) with a new idempotency key. Then check the proposal as for deprovision:

```bash
jq -r .patch "$OUT/proposal.json" | git -C ../fleet-clusters apply --check -v -
```

| Check | Expected result |
| --- | --- |
| Patch | Applies; changes only `provision/<id>/metadata.yaml`; adds it or only appends lines |
| Values | `summary` names each key's source; run values match the latest `post-provision` PipelineRun parameters |
| Commit message | Contains `[skip-provision]` only when the patch adds the file |
| A cluster with every key | Exit code `2`; `already declares every key` |
| `fleet-clusters` | `git status --porcelain` is empty |

Latest validation, run by the operator on 2026-10-24 against Aura commit `dfe32d4` and `fleet-clusters` commit `0453793`:

| Check | Result |
| --- | --- |
| Add file, `<cluster-name>` | Patch applied; `[skip-provision]` in the title; four keys in order; no retained post-provision run, so the run keys came from `fleet` defaults |
| Append keys, `<cluster-name>` | One whole-file hunk kept `htpasswd-provider-name: <provider-name>` and appended `platform`, `aws-credentials-mode`, and `openshift-ai-version`; no `[skip-provision]` |
| Run value, `<cluster-name>` | Its `metadata.yaml` lacked only `openshift-ai-version`; the proposal appended `v3` from its latest post-provision PipelineRun |
| Deprovisioned cluster, `<cluster-name>` | Refused: defined under `archive/` and no OpenShift resources |
| Contract, staleness, traces, records | All proposals validated, were fresh, and had matching trace digests; `fleet-clusters` was unchanged |
| `already declares every key` | Not exercised live; no cluster with all four keys was used |

## Pipeline Definition Validation

After the operator applies `deploy/` (`oc apply -k deploy`, see [Security](security.md#minimum-rbac)), confirm access:

```bash
oc --kubeconfig "$AURA_KUBECONFIG" auth can-i list pipelines.tekton.dev -n openshift-pipelines
```

Then read the `post-provision` definition, read-only:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync python -c 'import os, uuid; from pathlib import Path; from aura.collectors.openshift_cli_pipeline_client import OpenShiftCLIPipelineClient; from aura.collectors.pipeline_definitions import PipelineDefinitionCollector; s = PipelineDefinitionCollector(client=OpenShiftCLIPipelineClient(kubeconfig_path=Path(os.environ["AURA_KUBECONFIG"])), pipeline_names={"post-provision"}).collect(command_id=uuid.uuid4()); print(s.model_dump_json(indent=2))'
```

| Check | Expected result |
| --- | --- |
| `auth can-i` | `yes`; `create`, `patch`, and `delete` print `no` |
| Item | One `pipeline_definition` for `post-provision` |
| `required_params` | The parameters without defaults in `fleet`'s `tekton/pipelines/post-provision.yaml` |
| `malformed_params` | `0` |
| Defaults | No `default` field anywhere; `has_default` only; `redactions` empty |

Latest validation, run by the operator on 2026-10-24 after applying `deploy/` with `oc apply -k deploy`:

| Check | Result |
| --- | --- |
| Read access | `list` allowed on `clusterdeployments`, `managedclusters`, `pipelineruns`, `taskruns`, and on `pipelines` in `openshift-pipelines` |
| Least privilege | `get`, `create`, `patch`, and `delete` on Pipelines, `list` Pipelines in `default`, and `list` Secrets all denied |
| Definition | One `post-provision` item with 20 parameters, `malformed_params: 0`, no `default` field, and empty `redactions` |
| Required parameters | `cluster-name tier dns-zones`, matching `fleet` `HEAD` |
| Missing Pipeline | `PipelineDefinitionError: requested Pipelines not found: ['does-not-exist']`, after one `oc` timeout on a mobile hotspot |
| Existing commands | `inventory list --source combined` timed out on the hotspot; re-run on 2026-10-25 against `fleet-clusters` `a8a6bf0`, exit code `0`; see [Live OpenShift Validation](#live-openshift-validation) |

The snapshot `source_version` was `pipelines=`: `oc get -o json` returns a client-side `List` with an empty `resourceVersion`, so OpenShift and Tekton source versions carry no version. This affects every `oc` client and is tracked under [Status](status.md#not-implemented).

### Post-Provision Rerun Proposals

Choose a live, provisioned cluster with a finished `post-provision` run, and run [`propose post-provision-rerun`](cli.md#propose-post-provision-rerun) with a new idempotency key. Aura creates nothing.

```bash
jq '.pipeline_run' "$OUT/rerun.json"
```

| Check | Expected result |
| --- | --- |
| Manifest | `generateName: post-provision-<id>-` in `openshift-pipelines`; the source run's parameters, workspaces, and `taskRunTemplate`; the two `aura.openshiftpartnerlabs.com` annotations |
| Stored source run | `oc get pipelinerun <source> -n openshift-pipelines -o json` contains no field the evidence reports in `unsupported_fields` beyond zero values; the proposal is not refused for fields a rerun cannot copy |
| Source | `summary` names the latest finished `post-provision` run, or the `--pipeline-run` given |
| Parameters | Match the source run's `spec.params` (`oc get pipelinerun <name> -n openshift-pipelines -o json`) |
| A cluster with any unfinished `post-provision` run | Exit code `2`; `have not finished` |
| Hub | No new PipelineRun appears |

Partial validation, run by the operator on 2026-10-24 against Aura commit `a439dd7`: `<cluster-name>` was refused for conflicting Git definitions, which is correct. Its stored `post-provision` run passed evidence collection without any field a rerun cannot copy, confirming the zero-value handling on a real object. The refusal also reported a `metadata.yaml` conflict against defaults, which was misleading with two definitions; fixed in `01df26b`.

Latest validation, run by the operator on 2026-10-25 against Aura commit `30878cd` for `<cluster-name>`:

| Check | Result |
| --- | --- |
| Exit and source | Exit code `0`; source `post-provision-<cluster-name>-<suffix>`, the latest finished run |
| Manifest | `generateName: post-provision-<cluster-name>-` in `openshift-pipelines` with both Aura annotations |
| Parameters | `cluster-name`, `tier: virt`, `dns-zones`, `htpasswd-provider-name: <provider-name>`, and `openshift-ai-version: v3`, the five the `trigger-post-provision` task passes |
| `metadata.yaml` agreement | `<provider-name>` matches the declared value; `v3` matches the default for the absent key |
| Workspace and run template | Identical to `fleet` `9464f0b`: `shared-workspace` 1Gi `gp3-csi` claim template, `fleet-pipeline` service account, `fsGroup: 0`, and `fleet-pipeline-pull-secret` |

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
