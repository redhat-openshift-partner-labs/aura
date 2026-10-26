# Security and Access

## Security Model

Phase 1 is read-only:

| System | Access | Writes |
| --- | --- | --- |
| `fleet-clusters` | Committed `HEAD` of a local checkout | None |
| OpenShift | Read-only Kubernetes API | None |
| Tekton | Read-only Kubernetes API | None |
| RabbitMQ | None | None |
| Portal | None | None |
| External model provider | None | None |

The current diagnosis model is deterministic and local. No cluster-derived evidence is sent to an external model.

## Protected Repositories

Never modify:

- `fleet`
- `fleet-cluster-scaffold`
- `fleet-clusters`
- `portal`

The runtime reads `fleet-clusters`. `propose scaffold` also reads a local `fleet-cluster-scaffold` checkout and executes its code: it runs an export written byte for byte from `git ls-tree` and `git cat-file`, so export attributes such as `export-subst` and `export-ignore` do not apply, of the checkout's committed `HEAD`, never its working tree, with the current Python interpreter in `-I -B` mode, in a temporary directory, with no standard input and an environment of only `PATH`, `HOME` (that directory), and `LANG`, so no kubeconfig, cloud credential, or other operator variable reaches it, and with a five-minute timeout. An export refuses symlinks that point outside it. Treat the scaffold checkout as trusted code: Aura records its commit but cannot sandbox it beyond that. The definition check writes only to a `--shared --no-checkout` clone in a temporary directory. `fleet` and `portal` remain outside the runtime.

Every Git command Aura runs, in collectors and in the scaffold runner and check, drops inherited `GIT_` variables such as `GIT_DIR` and `GIT_INDEX_FILE`, disables hooks and fsmonitor with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, turns off optional locks so `git status` never rewrites an index, and gets no standard input. An operator environment or global Git configuration therefore cannot redirect Aura to another repository or run code through Git.

Git evidence resolves `HEAD` once and reads the tree listing and blobs from that pinned commit. Uncommitted working-tree changes, later commits, and untracked files are not treated as desired state.

## Operator Identity

Phase 1 accepts a non-empty, self-asserted `--operator` value. That value is the audit actor and CWA user identifier; it is not authentication and must not be treated as proof that a person holds the supplied roles. `--role` values are equally self-asserted: authorization evaluates only the supplied roles, defaulting to `viewer` when none are given, but does not verify them. This includes `proposer`: a stored proposal does not prove its operator holds that role, and Phase 3 approval must not rely on it. Operator authentication can be added later without changing the evidence contracts.

## Service Account

Use a dedicated service account such as:

```text
namespace: aura
name: aura-readonly
```

Store its kubeconfig separately, for example:

```text
$HOME/.kube/aura-readonly
```

Kubeconfig rules:

- Use a kubeconfig containing only the read-only service account.
- Do not copy an administrator kubeconfig.
- Do not commit the kubeconfig.
- Do not pass the kubeconfig content through `--query`.
- Keep the file permissions restricted to the local operator.

## Minimum RBAC

The service account needs only `list` on five resources. Aura always lists resources and does not request individual objects by name. The manifests are in [`deploy/`](../deploy) and target the OpenShift hub:

| Manifest | Grants |
| --- | --- |
| `namespace.yaml`, `serviceaccount.yaml` | The `aura` namespace and the `aura-readonly` service account |
| `clusterrole.yaml`, `clusterrolebinding.yaml` | `list` on `clusterdeployments.hive.openshift.io`, `managedclusters.cluster.open-cluster-management.io`, and `pipelineruns` and `taskruns` in `tekton.dev`, cluster-wide |
| `pipelines-role.yaml`, `pipelines-rolebinding.yaml` | `list` on `pipelines.tekton.dev` in `openshift-pipelines` only, for prerequisite P2 |

`tests/test_deploy_manifests.py` keeps every rule `list`-only and the granted resources equal to the resources the `oc` clients query, so a collector that reads a new resource fails the suite until the manifests grant it. The manifests create no token Secret; build the read-only kubeconfig from a token for the service account as before.

Preview the change on the hub without applying it, then apply it as a cluster administrator:

```bash
oc apply -k deploy --dry-run=server
```

```bash
oc apply -k deploy
```

If `aura-readonly` objects already exist, `apply` replaces their rules with these. Until the Pipeline Role and RoleBinding are applied, only the Pipeline definition collector fails; every current command works without them.

Verify authorization:

```bash
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list clusterdeployments.hive.openshift.io -A
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list managedclusters.cluster.open-cluster-management.io
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list pipelineruns.tekton.dev -A
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list taskruns.tekton.dev -A
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list pipelines.tekton.dev -n openshift-pipelines
```

Every command must print `yes`. Write verbs such as `create`, `patch`, and `delete` must print `no`.

## Secret Redaction

`RedactionPolicy` removes values for these field names:

- `access_token`
- `api_key`
- `authorization`
- `aws_secret_access_key`
- `bearer_token`
- `client_secret`
- `cookie`
- `kubeconfig`
- `password`
- `private_key`
- `pull_secret`
- `refresh_token`
- `secret`
- `secret_access_key`
- `ssh_private_key`
- `token`

Matching ignores case and non-alphanumeric separators, so `pullSecret`, `pull-secret`, and `PULL_SECRET` all match `pull_secret`. The normalized name must match exactly, so references such as `pullSecretRef` are kept. Matching is recursive through mappings and lists. Removed field paths are recorded without their values.

PipelineRun parameters carry values under generic `name` and `value` keys, so field-name matching cannot protect them. `redact_parameters` redacts any parameter whose normalized name contains `apikey`, `credential`, `kubeconfig`, `passwd`, `password`, `privatekey`, `secret`, or `token`. These fleet parameters are kept because they hold a display name, a Secret name, or a file path, not secret material:

- `htpasswd-provider-name`, the identity provider display name
- `keycloak-admin-secret`, a Secret name
- `spoke-kubeconfig`, a file path

Add a parameter to that allowlist only after confirming its value can never be secret material. Workspace bindings record Secret and ConfigMap names, never their contents.

Redaction is a defense in depth. Collectors should still avoid reading secret values in the first place.

## Model Boundary

Every future model call must:

1. Consume a frozen CWA snapshot.
2. Return strict `DiagnosisResponse` JSON.
3. Be validated by deterministic code.
4. Remain unauthorized to execute anything.

Model output is never treated as authority. Authorization and any future execution guards remain deterministic code outside the model.

## Record Integrity

All current stores use exclusive, atomic publication:

- Existing snapshots are not overwritten.
- Existing audit events are not overwritten.
- Existing terminal command records are not overwritten.
- Existing CWA records are not overwritten.
- Existing diagnoses are not overwritten.
- Existing proposals are not overwritten. A stored proposal fails to load when its content no longer matches its digest or when its `proposal_digest` is missing or blank, because validation would otherwise recompute it.

The evidence, CWA, audit, diagnosis, and proposal stores also fail to load a record whose own ID differs from its file name, so a record copied or renamed under another valid ID is rejected rather than reported under the wrong ID. The command store is separate: it treats a record whose idempotency key differs from its file name as not found.

Snapshots and responses include SHA-256 digests over canonical JSON.
Records are serialized to a private temporary file, flushed with `fsync`, and linked into their final name only after the complete write. A failed write leaves no partial final record. When a record write fails with an operating-system error, the command is stored as `failed` if the command store is writable, and the CLI exits `2`.

## Operational cautions

- Use one idempotency key per operator request. Succeeded, failed, and refused keys are terminal and are suppressed through `--command-dir`; a new attempt requires a new key.
- Treat `oc` failures as connectivity, RBAC, or cluster-availability errors.
- Do not reuse output directories for commands that may produce the same deterministic UUIDs unless duplicate-record errors are expected.
- Do not add an external model provider without an explicit data-flow and approval review.
- `propose scaffold` reads the scaffold's standard output and error and every generated file into memory without size limits; run it only with a trusted scaffold checkout.
