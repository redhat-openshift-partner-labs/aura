# Validation

## Automated Tests

Run:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
```

Expected current result:

```text
553 passed
```

Run the CI workflow locally with `act` before reporting work ready to publish; see [Run the CI Workflow Locally](development.md#9-run-the-ci-workflow-locally). The latest run, at `fcd86d1`, passed with 553 tests on Python 3.13.17 in `linux/arm64`.

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
- Cluster-edit generation over real temporary Git trees: added and appended `metadata.yaml` files that apply with `git apply --check`, preserve existing bytes, and leave a complete definition; values from runs and defaults; every refusal reason.
- Deprovision contract rejection of non-empty or non-regular sentinel files.
- The failure-injection matrix: every command's documented write order, a failure of each write in turn before writing and after linking, and each route's read errors, with invariants I1–I4 checked after every run.
- Fixture validity and secret redaction, including field spelling variants.

## Local Fixture

This procedure is pinned to the `fleet-clusters` commit below; `0171d46a-scopedaws` has since been deprovisioned, so current `HEAD` no longer matches these results. The automated tests use a static copy of this fixture.

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

The cluster results below are historical, recorded before `0171d46a-scopedaws` was deprovisioned; current cluster states differ. Run the same command with new idempotency keys against clusters in comparable states to validate refusals:

| Cluster | Expected result |
| --- | --- |
| `2dcb6934-thalescs` | Exit code `0`; summary names a succeeded PipelineRun |
| `423d216c-vistergy` | Exit code `2`; refused with `Git cluster definition files` and `Tekton PipelineRun status` |
| `1bd62dc5-hpezerto` | Exit code `2`; refused with `OpenShift actual state` and `Tekton PipelineRun status` |
| `pchriste` | Exit code `2`; refused with `Conflicting Git cluster definitions` |
| `10f25620-vscopeocpv` | Exit code `0`; stranded deprovision summary citing its retained deprovision PipelineRun, confidence `0.9` |
| `264c5202-codvoqs` | Exit code `0`; stranded deprovision summary stating `no PipelineRun is retained for it`, confidence `0.6` |
| `f6f3eba2-redisvirt` | Exit code `2`; leftover sentinel, refused with `Git cluster definition files` and `OpenShift actual state` |

Historical validation observed:

| Check | Result |
| --- | --- |
| Fixture diagnosis | Exit code `0`; summary names PipelineRun `post-provision-0171d46a-scopedaws-p4w4v` as succeeded with confidence `0.9` |
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

Latest validation, run by the operator on 2026-10-23 against Aura commit `c5abafb` with `virt-cookbook` as the live cluster and `0171d46a-scopedaws` for refusal: every check above produced the expected result.

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
| Add file, `423d216c-vistergy` | Patch applied; `[skip-provision]` in the title; four keys in order; no retained post-provision run, so the run keys came from `fleet` defaults |
| Append keys, `virt-cookbook` | One whole-file hunk kept `htpasswd-provider-name: VirtCookbook` and appended `platform`, `aws-credentials-mode`, and `openshift-ai-version`; no `[skip-provision]` |
| Run value, `10a9746b-virtualc` | Its `metadata.yaml` lacked only `openshift-ai-version`; the proposal appended `v3` from its latest post-provision PipelineRun |
| Deprovisioned cluster, `0171d46a-scopedaws` | Refused: defined under `archive/` and no OpenShift resources |
| Contract, staleness, traces, records | All proposals validated, were fresh, and had matching trace digests; `fleet-clusters` was unchanged |
| `already declares every key` | Not exercised live; no cluster with all four keys was used |

## Pipeline Definition Validation

After the operator applies the P2 Role and RoleBinding from [Security](security.md#minimum-rbac), confirm access:

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

Not yet run live.

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
