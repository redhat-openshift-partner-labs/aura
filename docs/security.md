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

The runtime reads `fleet-clusters`. `propose scaffold` also reads a local `fleet-cluster-scaffold` checkout and executes its code: it runs an export written byte for byte from `git ls-tree` and `git cat-file`, so export attributes such as `export-subst` and `export-ignore` do not apply, of the checkout's committed `HEAD` (the working tree is excluded), with the current Python interpreter in `-I -B` mode, in a temporary directory, with no standard input and an environment of only `PATH`, `HOME` (that directory), and `LANG`, so no kubeconfig, cloud credential, or other operator variable reaches it, and with a five-minute timeout. An export refuses symlinks that point outside it. Treat the scaffold checkout as trusted code: Aura records its commit but cannot sandbox it beyond that. The definition check writes only to a `--shared --no-checkout` clone in a temporary directory. `fleet` and `portal` remain outside the runtime.

Every Git command Aura runs, in collectors and in the scaffold runner and check, drops inherited `GIT_` variables such as `GIT_DIR` and `GIT_INDEX_FILE`, disables hooks and fsmonitor with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, turns off optional locks so `git status` never rewrites an index, and gets no standard input. An operator environment or global Git configuration therefore cannot redirect Aura to another repository or run code through Git.

Git evidence resolves `HEAD` once and reads the tree listing and blobs from that pinned commit. Uncommitted working-tree changes, later commits, and untracked files are not treated as desired state.

## Operator Identity

Phase 1 accepts a non-empty, self-asserted `--operator` value. That value is the audit actor and CWA user identifier; it is not authentication and must not be treated as proof that a person holds the supplied roles. `--role` values are equally self-asserted: authorization evaluates only the supplied roles, defaulting to `viewer` when none are given, but does not verify them. This includes `proposer`: a stored proposal does not prove its operator holds that role, and Phase 3 approval must not rely on it.

Phase 3 approvals and execution requests are signed with SSH keys instead ([D7](plans/06-phase-3-approved-execution.md#operator-decisions)). `aura.signing` signs and verifies with `ssh-keygen -Y` through `run_process`. A signature counts only when the allowed-signers list names its key for exactly one principal, that principal is the one the record claims, and the key may sign the record's namespace, `aura-approve` or `aura-execute`. A key listed for more than one principal is refused, so one key cannot count as two approvers. Verification runs at a given time, so a key retired by setting `valid-before` in the list still verifies records signed before that time. When the store loads a signed record, it refuses one whose `created_at` is more than five minutes after the row's `stored_at`, and one whose key in the list has no `namespaces` option. The list is read from ConfigMap `aura-allowed-signers` in namespace `aura`, which only hub administrators can edit; tests pass a list directly, and the CLI has no option for one.

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

The service account needs `list` on five resources, and `get` on two ConfigMaps by name for Phase 3. Collectors always list resources; only the Phase 3 policy reader gets objects by name. The manifests are in [`deploy/`](../deploy) and target the OpenShift hub:

| Manifest | Grants |
| --- | --- |
| `namespace.yaml`, `serviceaccount.yaml` | The `aura` namespace and the `aura-readonly` service account |
| `clusterrole.yaml`, `clusterrolebinding.yaml` | `list` on `clusterdeployments.hive.openshift.io`, `managedclusters.cluster.open-cluster-management.io`, and `pipelineruns` and `taskruns` in `tekton.dev`, cluster-wide |
| `pipelines-role.yaml`, `pipelines-rolebinding.yaml` | `list` on `pipelines.tekton.dev` in `openshift-pipelines` only, for prerequisite P2 |
| `policy-role.yaml`, `policy-rolebinding.yaml` | `get` on ConfigMaps `aura-allowed-signers` and `aura-execution-store` in namespace `aura`, by name only, for Phase 3. Both are read in one `oc get`: the allowed-signers list, the execution store's `deployment_id`, and the optional `pipeline_run_retention_seconds` |

`tests/test_deploy_manifests.py` keeps every rule `list`-only except the policy Role, which must grant exactly `get` on the two policy ConfigMaps, and keeps the granted resources equal to the resources the `oc` clients query, so a collector that reads a new resource fails the suite until the manifests grant it. The manifests create no token Secret; build the read-only kubeconfig from a token for the service account as before.

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
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i get configmap/aura-allowed-signers -n aura
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i get configmap/aura-execution-store -n aura
```

Every command must print `yes`. Write verbs such as `create`, `patch`, and `delete` must print `no`, and so must `get` on any other ConfigMap, for example `oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i get configmap/other -n aura`.

## Staging Database

[`deploy/postgresql/`](../deploy/postgresql) deploys PostgreSQL for the Phase 3 [Execution Store](plans/06-phase-3-approved-execution.md#execution-store) in staging and development (D13). It is a separate kustomization, so `oc apply -k deploy` never deploys it. Production enablement, including TLS, backups, and high availability, is out of its scope.

| Manifest | Creates |
| --- | --- |
| `namespace.yaml` | Namespace `aura-db`, separate from `aura`, so access granted to Aura operators in `aura` does not reach the database's Secret, pods, or volume |
| `service.yaml` | Headless Service `aura-postgresql` on port 5432 |
| `statefulset.yaml` | One-replica StatefulSet running the official image `docker.io/library/postgres:18.6-trixie`, with a 10Gi volume from the default StorageClass mounted at `/var/lib/postgresql`, which PostgreSQL 18 images declare. The pod runs under OpenShift's restricted-v2 SCC with no service account token, and creates database `aura` |

`tests/test_postgresql_manifests.py` keeps the image pinned to an exact official release, the volume at the image's declared path, the password in a mounted Secret, and every object in `aura-db`.

Prerequisites: cluster-administrator access to the hub, and a hub that can pull from Docker Hub.

Create the namespace and the superuser Secret as the database administrator. The password is generated into the Secret and never appears on a command line:

```bash
oc apply -f deploy/postgresql/namespace.yaml
```

```bash
openssl rand -base64 32 | tr -d '\n' | oc -n aura-db create secret generic aura-postgresql --from-file=password=/dev/stdin
```

Preview and apply the database:

```bash
oc apply -k deploy/postgresql --dry-run=server
```

```bash
oc apply -k deploy/postgresql
```

Wait until the pod is ready:

```bash
oc -n aura-db rollout status statefulset/aura-postgresql
```

The Secret is read only on the first start, when the data directory is empty; changing it later does not change the superuser password. The Service is reachable only inside the cluster. For staging, the administrator reaches it with `oc -n aura-db port-forward statefulset/aura-postgresql 5432:5432`. The administrator creates role `aura_operator` and one login per operator in it, then runs `aura/execution/postgresql/grants.sql`, which grants the role insert-only access (task 3.7); task 0.3 records the staging store's connection in [Validation](validation.md).

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

PipelineRun parameters carry values under generic `name` and `value` keys, so field-name matching cannot protect them. `redact_parameters` redacts any parameter whose normalized name contains `apikey`, `credential`, `kubeconfig`, `passwd`, `password`, `privatekey`, `secret`, or `token`. These fleet parameters are kept because they hold a display name, a Secret name, or a file path; none holds secret material:

- `htpasswd-provider-name`, the identity provider display name
- `keycloak-admin-secret`, a Secret name
- `spoke-kubeconfig`, a file path

Add a parameter to that allowlist only after confirming its value can never be secret material. Workspace bindings record Secret and ConfigMap names and omit their contents.

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

The evidence, CWA, audit, diagnosis, and proposal stores also fail to load a record whose own ID differs from its file name, so a record copied or renamed under another valid ID is rejected; Aura never reports it under the wrong ID. The command store is separate: it treats a record whose idempotency key differs from its file name as not found.

Snapshots and responses include SHA-256 digests over canonical JSON.
`aura.provenance` loads a proposal or a diagnosis with every record it references, its CWA trace, CWA snapshot, evidence snapshot, and the trace's audit event, and refuses any reference whose ID or digest differs from the stored target. Every store revalidates a record before writing it, so a copy whose digests no longer match its fields is refused; Aura stores no unreadable copy. Stored records are parsed only in `json_store`. `tests/test_architecture.py` fails for a JSON, YAML, or pydantic JSON parse, through any import alias, anywhere else that is not on its allow list, and for a store that overrides how records load. The allow list holds `oc` output, `fleet-clusters` YAML, and `cli._names_another_command`, which reads only the `command_id` of an audit record that failed to load.
Records are serialized to a private temporary file, flushed with `fsync`, and linked into their final name only after the complete write. A failed write leaves no partial final record. When a record write fails with an operating-system error, the command is stored as `failed` if the command store is writable, and the CLI exits `2`.

## Execution Store Integrity

The [execution store](architecture.md#execution-store) never updates or deletes a row; `tests/test_execution_store.py` traces every statement a session runs and fails on `UPDATE` or `DELETE`. A row whose record, digest, or copied columns were edited is refused on load. On PostgreSQL, `aura/execution/postgresql/grants.sql` limits role `aura_operator` to `INSERT` and `SELECT`; `tests/test_postgresql_store.py` connects as a member of it and requires `UPDATE`, `DELETE`, `TRUNCATE`, DDL, and a schema-version insert to fail, and requires an insert that writes the `login` column of `commands` to fail, so a command row always carries the session's login. The tests also cover signature and column verification on load, read-back of a lost commit, a race of two dispatches for one cluster, the hourly dispatch window, and UTC `stored_at` under a non-UTC database time zone.

## Operational cautions

- Use one idempotency key per operator request. Succeeded, failed, and refused keys are terminal and are suppressed through `--command-dir`; a new attempt requires a new key.
- Treat `oc` failures as connectivity, RBAC, or cluster-availability errors.
- Do not reuse output directories for commands that may produce the same deterministic UUIDs unless duplicate-record errors are expected.
- Do not add an external model provider without an explicit data-flow and approval review.
- `propose scaffold` reads the scaffold's standard output and error and every generated file into memory without size limits; run it only with a trusted scaffold checkout.
