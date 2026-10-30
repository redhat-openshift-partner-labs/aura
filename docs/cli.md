# CLI Reference

Run Aura through `uv`:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura COMMAND
```

All successful commands print JSON to standard output.

## Common Flags

| Flag | Required | Default | Acceptable values | Purpose |
| --- | --- | --- | --- | --- |
| `--operator` | Yes | None | Any non-empty operator identifier | Records the self-asserted command actor |
| `--idempotency-key` | Yes | None | Any non-empty key unique to one operator request | Recorded in the command envelope |
| `--command-dir` | Yes | None | Writable directory | Stores terminal idempotency-key records |
| `--role` | No | `viewer` when no `--role` is supplied | `viewer`, `proposer`, `approver`, `executor`, `admin` | Roles used for authorization; may be repeated; supplied roles replace the default |
| `--query` | No | Command-specific | Any string | Recorded in command input and CWA interaction slot |
| `--snapshot-dir` | No | None | Writable directory | Store the evidence snapshot |
| `--audit-dir` | No | None | Writable directory | Store audit events |

Phase 1 commands are authorized for `viewer` or `admin`. `propose` commands are authorized for `proposer` or `admin`, so the default `viewer` role is rejected. `proposal status` is authorized for `viewer`, `proposer`, or `admin`. A command whose supplied roles include none of its allowed roles is rejected before evidence collection.

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
- `cluster_definition_invalid`
- `deprovision_sentinel`

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

- Git definition and `deprovision_sentinel` items when Git is enabled.
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
  --cluster <cluster> \
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

## `propose deprovision`

| Flag | Required | Default | Acceptable values |
| --- | --- | --- | --- |
| `--cluster` | Yes | None | DNS-1123 label that does not contain `-archive` |
| `--fleet-clusters` | Yes | None | Path to a local `fleet-clusters` checkout |
| `--openshift-kubeconfig` | Yes | None | Path to a read-only kubeconfig |
| `--snapshot-dir` | Yes | None | Writable directory |
| `--audit-dir` | Yes | None | Writable directory |
| `--cwa-dir` | Yes | None | Writable directory |
| `--proposal-dir` | Yes | None | Writable directory |
| `--query` | No | `propose deprovision` | Any string |

Supply `--role proposer` or `--role admin`.

Example:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura propose deprovision \
  --cluster <cluster> \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --snapshot-dir ./local/snapshots \
  --audit-dir ./local/audit \
  --cwa-dir ./local/cwa \
  --proposal-dir ./local/proposals \
  --role proposer \
  --operator operator@example.com \
  --idempotency-key propose-deprovision-001 \
  --command-dir ./local/commands
```

The command prints the stored `Proposal`. Its `patch` adds only the empty file `deprovision/<id>-archive`, and its `commit_message` must be used for a standalone commit. Aura writes nothing to `fleet-clusters`; merging the commit is the execution step and starts the deprovision pipeline.

If a precondition is unmet, the command exits with code `2`, prints `proposal refused:` and every unmet precondition to standard error, stores no proposal, and still stores evidence, CWA, and audit records. A `--cluster` value that is not a DNS-1123 label, or that contains `-archive` anywhere, is rejected before collection, for every `propose` subcommand, because `fleet` removes every `-archive` substring when it reads a deprovision sentinel.

## `propose cluster-edit`

Flags are the same as [`propose deprovision`](#propose-deprovision); the `--query` default is `propose cluster edit`. Supply `--role proposer` or `--role admin`.

Example:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura propose cluster-edit \
  --cluster <cluster> \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --snapshot-dir ./local/snapshots \
  --audit-dir ./local/audit \
  --cwa-dir ./local/cwa \
  --proposal-dir ./local/proposals \
  --role proposer \
  --operator operator@example.com \
  --idempotency-key propose-cluster-edit-001 \
  --command-dir ./local/commands
```

The command prints the stored `Proposal`. Its `patch` either adds `provision/<id>/metadata.yaml` with `platform`, `aws-credentials-mode`, and `htpasswd-provider-name`, plus `openshift-ai-version` when the cluster's tier is `ai`, or appends the keys an existing `metadata.yaml` lacks without changing any existing line. `fleet` reads `openshift-ai-version` only for `ai` clusters, so it is never added for another tier. Its `summary` names the source of every value: the ClusterDeployment patch, the latest `post-provision` PipelineRun, or the `fleet` default.

A patch that adds `metadata.yaml` has a commit message containing `[skip-provision]`. That commit must be pushed alone as the push head commit, so squash or fast-forward it; a merge commit becomes the head commit and does not carry the marker. A patch that only appends keys starts no pipeline.

Unmet preconditions refuse the command with every reason, as for `propose deprovision`. See [Cluster-Edit Proposal](evidence.md#cluster-edit-proposal) for the value rules and refusal reasons.

## `propose post-provision-rerun`

Flags are the same as [`propose deprovision`](#propose-deprovision), plus `--pipeline-run`; the `--query` default is `propose post provision rerun`. Supply `--role proposer` or `--role admin`. It is a `propose.pipeline` command.

| Flag | Required | Default | Acceptable values |
| --- | --- | --- | --- |
| `--pipeline-run` | No | The latest `post-provision` run for the cluster | Non-empty PipelineRun name or UID |

Example:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura propose post-provision-rerun \
  --cluster <cluster> \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --snapshot-dir ./local/snapshots \
  --audit-dir ./local/audit \
  --cwa-dir ./local/cwa \
  --proposal-dir ./local/proposals \
  --role proposer \
  --operator operator@example.com \
  --idempotency-key propose-rerun-001 \
  --command-dir ./local/commands
```

The command prints the stored `Proposal`. Its `pipeline_run` is a PipelineRun manifest with `generateName: post-provision-<id>-` in `openshift-pipelines` that copies the source run's parameters, workspaces, and run template. Aura never creates it; Phase 3 would create it through the Kubernetes API and would not use the EventListener. It reads Pipelines, so `deploy/` must be applied; a missing `post-provision` Pipeline fails the command.

Unmet preconditions refuse the command with every reason. See [Post-Provision Rerun Proposal](evidence.md#post-provision-rerun-proposal).

## `propose scaffold`

Proposes a new cluster definition generated by a pinned local `fleet-cluster-scaffold` checkout. Flags are those of [`propose deprovision`](#propose-deprovision), where `--cluster` names the new cluster, plus the flags below; the `--query` default is `propose scaffold`. Supply `--role proposer` or `--role admin`. It is a `propose.patch` command.

| Flag | Required | Default | Acceptable values |
| --- | --- | --- | --- |
| `--scaffold` | Yes | None | The top directory of a clean `fleet-cluster-scaffold` Git checkout |
| `--region` | Yes | None | An AWS region name, such as `us-east-2` |
| `--tier` | Yes | None | `base`, `virt`, or `ai` |
| `--cloud` | No | `aws` | `aws`; any other value is refused |
| `--topology`, `--environment`, `--base-domain`, `--control-plane-type`, `--worker-type`, `--control-plane-replicas`, `--worker-replicas`, `--image-set`, `--zones`, `--install-attempts-limit`, `--htpasswd-provider-name` | No | The scaffold's default | Passed to the scaffold unchanged, in this order |

`kustomize` must be on `PATH`. Aura runs an export written byte for byte from `git ls-tree` and `git cat-file`, so export attributes such as `export-subst` and `export-ignore` do not apply, of the scaffold's `HEAD` with the current Python interpreter, in a temporary workspace holding such an export of `fleet-clusters` at its `HEAD`, with no standard input, an environment of only `PATH`, `HOME`, and `LANG`, and a five-minute timeout. It never passes `--force`, `--skip-validation`, or `--legacy-mint-credentials`. It then applies the generated patch to a shared clone of `fleet-clusters` and requires one complete definition. Neither checkout changes.

Example:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura propose scaffold \
  --cluster <new-cluster> \
  --region us-east-2 \
  --tier base \
  --topology sno \
  --scaffold ../fleet-cluster-scaffold \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --snapshot-dir ./local/snapshots \
  --audit-dir ./local/audit \
  --cwa-dir ./local/cwa \
  --proposal-dir ./local/proposals \
  --role proposer \
  --operator operator@example.com \
  --idempotency-key propose-scaffold-001 \
  --command-dir ./local/commands
```

The command prints the stored `Proposal`. Its `patch` adds `provision/<id>/`; merging it starts pre-provision, provision, and post-provision. Unmet preconditions and unusable scaffold output, including any change other than adding files, refuse the command with every reason. A scaffold that cannot run at all, such as on a timeout, standard output or error over 1 MiB, a generated file over 1 MiB or generated files over 8 MiB in total, a missing commit, a symlink in an export that points outside it, or a generated file that is not UTF-8, fails the command. Undecodable bytes in the scaffold's standard output or error are replaced and do not fail the command. See [Scaffold Proposal](evidence.md#scaffold-proposal).

## `proposal status`

| Flag | Required | Default | Acceptable values |
| --- | --- | --- | --- |
| `--fleet-clusters` | Yes | None | Path to a local `fleet-clusters` checkout |
| `--proposal-dir` | Yes | None | Directory of stored proposals |
| `--proposal-id` | No | All stored proposals | Proposal UUID; may be repeated |
| `--openshift-kubeconfig` | No | None | Read-only kubeconfig; with it, each item reports its Phase 3 derived state |
| `--query` | No | `check proposal staleness` | Any string |

Example:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura proposal status \
  --fleet-clusters ../fleet-clusters \
  --proposal-dir ./local/proposals \
  --operator operator@example.com \
  --idempotency-key proposal-status-001 \
  --command-dir ./local/commands
```

The command prints an evidence snapshot with one `proposal_staleness` item per proposal, ordered by proposal ID, all checked against one pinned `fleet-clusters` `HEAD`. A stale proposal is a successful result; the command exits `0`. A missing or unreadable `--proposal-dir`, a missing requested proposal, a `.json` file whose name is not a proposal ID, a record that cannot be read, a record whose digest no longer matches or is blank, and a record whose ID differs from its file name each exit `2` and store a `failed` command record. Repeated `--proposal-id` values are checked once.

With `--openshift-kubeconfig`, the command also opens the execution store that Aura's configuration file names, reads the hub's allowed-signers list in one `oc` call, and reports for each proposal whether it is published and its derived state. Approvals are verified against the hub list when they are read, so an approval whose signature the list no longer accepts fails the command. The derived state is the first that applies: `rejected` when any rejection names the proposal; `stale` when it is Git-stale; `proposal_ready` when it is unpublished or has no approval records; `approved` when unexpired approvals of its exact `proposal_digest` and action digest come from enough distinct principals, two for `deprovision` and one otherwise ([D11](plans/06-phase-3-approved-execution.md#operator-decisions)); otherwise `awaiting_approval`, which includes a proposal whose approvals have all expired. Without `--openshift-kubeconfig`, `published` and `derived_state` are `null`.

The check is Git-only and covers every proposal type, reruns included: it does not detect live OpenShift or Tekton state that has changed since the proposal's evidence was collected. The Phase 3 execution guard re-collects that state before executing. Rerun proposals stored before `base_commit` became required for reruns no longer load, and one such record fails every `proposal status` run that does not name its proposals with `--proposal-id`. Aura never deletes or overwrites a record, so move the old record out of `--proposal-dir` and generate the proposal again.

## `proposal submit`

Publishes a stored Phase 2 proposal to the Phase 3 [execution store](architecture.md#execution-store), so approvers and executors on other machines read the same records. Requires `proposer` or `admin`. The store comes only from Aura's configuration file; only the SQLite store is available until Phase 3 task 3.7.

```bash
uv run --no-sync aura proposal submit \
  --proposal-id <proposal-id> \
  --proposal-dir ./local/proposals \
  --cwa-dir ./local/cwa \
  --snapshot-dir ./local/snapshots \
  --audit-dir ./local/audit \
  --command-dir ./local/commands \
  --operator you@example.com \
  --idempotency-key submit-001 \
  --role proposer
```

The directories hold the Phase 2 proposal, its provenance, and its command record; `--command-dir` here names the Phase 2 command records, and the submit command's own record goes to the store. The command loads the proposal with its CWA trace, CWA snapshot, evidence snapshot, and audit events, verifying every ID and digest; finds its command record, which must be `succeeded`; and builds a [`PublishedProposal`](evidence.md#published-proposal). It refuses a missing or invalid proposal, a broken reference, a missing or unsucceeded command record, and a proposal already published. On success it stores `command_received`, `proposal_published`, the publication, and the terminal command record in that order, and prints the publication.

Its idempotency key is unique per store login. A reused key exits `2` before any write. A failed store write stores `failed` and `command_failed` linked to the last event stored.

## `proposal approve` and `proposal reject`

Records a signed approval or rejection of a published proposal ([D7, D11](plans/06-phase-3-approved-execution.md#operator-decisions)). Neither command takes `--operator` or `--role`: identity is the principal in the `[signing]` section of Aura's configuration file, proven by an SSH signature that the hub's allowed-signers list must accept.

```toml
[signing]
principal = "<principal in the allowed-signers list>"
key = "/absolute/path/to/id_ed25519.pub"
agent = true
```

With `agent = true`, `key` is the public key of a private key loaded in `ssh-agent`; with `agent = false`, it is a private key file.

```bash
uv run --no-sync aura proposal approve \
  --proposal-id <proposal-id> \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --idempotency-key approve-001
```

`proposal reject` takes the same flags. The command reads the published proposal from the store, reads ConfigMap `aura-allowed-signers` from the hub in one `oc` call, computes the proposal's [action digest](plans/06-phase-3-approved-execution.md#action-subset), and builds an [`ApprovalRecord`](evidence.md#approval-record) whose `audit_event_digest` is the digest of its `approval_recorded` event. It signs the record with namespace `aura-approve` and verifies the signature against the hub list before writing anything but `command_received` and its refusal. An approval expires 24 hours after it is signed; a rejection has no expiry. It refuses a proposal that is not published and a signature the hub list does not accept, and fails when the hub cannot be read. On success it stores `command_received`, `approval_recorded`, the approval, and the terminal command record in that order, and prints the approval.

## `execute`

Dispatches one approved, published proposal through the [Command Guard](architecture.md#command-guard). Like approve, it takes identity from the `[signing]` section of Aura's configuration file; the key must be listed for `aura-execute`. Until Stages 4 and 5 add the real adapters, only the test-only `recording` adapter dispatches, when `[execution] adapters` enables it.

```bash
uv run --no-sync aura execute \
  --proposal-id <proposal-id> \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --fleet-clusters ../fleet-clusters \
  --idempotency-key execute-001 \
  --dry-run
```

`--scaffold` names the scaffold checkout for a scaffold proposal. The command reads the hub signer list and runs G1–G12. G8 re-collects the evidence and stores it with the CWA snapshot, the CWA trace, and the regenerated proposal, so these records exist for a dry run and for a later refusal. With `--dry-run` the command stops after the guard and prints the adapter request. Otherwise it stores `execution_started` and the signed `dispatching` record in a locked transaction that rechecks G5, G6, and G11; calls the adapter; and stores `execution_finished`, the result record, and the terminal command record. A guard refusal names its rule. A proposal is dispatched at most once: a second `execute` is refused by G6. When the adapter reports no result, or a write after the dispatch fails, the `dispatching` record stands, the execution is `awaiting_operator`, and the message names `aura execution status`.

## Output and Exit Codes

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `2` | Argument error, unauthorized command, execution failure, diagnosis or proposal refusal, duplicate record, idempotency-key reuse, or internal error |
| Other non-zero | An error raised before the idempotency check that Aura does not handle, which is a bug |

An `oc` failure is reported as a concise CLI error with exit code `2`; no partial JSON is printed.

Additional error contracts:

- Blank `--operator` and `--idempotency-key` values are rejected before collection.
- A proposal generator output that the `Proposal` contract rejects exits `2` with `generated proposal is invalid:` and stores a `failed` command record.
- Existing non-directory record paths are rejected before collection.
- A missing Git checkout is reported as an unusable checkout.
- Git subprocess failures report the exit code and final diagnostic line.
- Missing or inaccessible `oc`, permission failures, invalid JSON, a response without an `items` list, a list without a string `metadata.resourceVersion` when its version is read, output that is not UTF-8, and command timeouts are converted to concise `OpenShiftCLIError` messages.
- A command that would make more than 10 `oc` calls exits `2` with `oc call budget of 10 exceeded` and stores a `failed` command record.
- Record write failures, such as a full disk or an unwritable directory, exit `2` with the operating-system error and store a `failed` terminal record when `--command-dir` is writable.
- An unreadable or invalid record for the requested idempotency key, including one without its stored digests or one that holds another key, exits `2` before collection with `idempotency key record is unreadable`.
- A `--command-dir` that cannot be created or locked exits `2` before collection.
- An error a command does not handle, raised after the idempotency check, exits `2` with `aura: internal error: <type>: <message>` and no traceback. It stores a `failed` command record unless a terminal record for the key is already stored, or a `refused` record when the command's audit chain already ends with `command_refused`, and the `command_failed` reason names only the error type. An `OSError` it does not handle is a filesystem error instead: with an errno it prints and stores `filesystem error: <error>`, which names the errno, its text, and the path; without one it stores only `filesystem error: <type>`. A scaffold whose working directory is missing reports `cannot use working directory <path>` and does not report `cannot run`; see [Wiring](wiring.md#record-wiring).
