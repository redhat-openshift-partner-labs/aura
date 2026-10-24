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

The current runtime only reads `fleet-clusters`. The other protected repositories remain outside the Phase 1 runtime.

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

The service account needs only `list` on five resources. Aura always lists resources and does not request individual objects by name. Pipelines are read only in `openshift-pipelines`, so that grant is a namespaced Role rather than part of the ClusterRole:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: aura-readonly
rules:
- apiGroups: ["hive.openshift.io"]
  resources: ["clusterdeployments"]
  verbs: ["list"]
- apiGroups: ["cluster.open-cluster-management.io"]
  resources: ["managedclusters"]
  verbs: ["list"]
- apiGroups: ["tekton.dev"]
  resources: ["pipelineruns", "taskruns"]
  verbs: ["list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: aura-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: aura-readonly
subjects:
- kind: ServiceAccount
  name: aura-readonly
  namespace: aura
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: aura-readonly-pipelines
  namespace: openshift-pipelines
rules:
- apiGroups: ["tekton.dev"]
  resources: ["pipelines"]
  verbs: ["list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: aura-readonly-pipelines
  namespace: openshift-pipelines
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: aura-readonly-pipelines
subjects:
- kind: ServiceAccount
  name: aura-readonly
  namespace: aura
```

The Pipeline Role and RoleBinding were added on 2026-10-24 for prerequisite P2. Until the operator applies them, only the Pipeline definition collector fails; every current command works without them.

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
- Existing proposals are not overwritten, and a proposal whose content no longer matches its digest fails to load.

Snapshots and responses include SHA-256 digests over canonical JSON.
Records are serialized to a private temporary file, flushed with `fsync`, and linked into their final name only after the complete write. A failed write leaves no partial final record. When a record write fails with an operating-system error, the command is stored as `failed` if the command store is writable, and the CLI exits `2`.

## Operational cautions

- Use one idempotency key per operator request. Succeeded, failed, and refused keys are terminal and are suppressed through `--command-dir`; a new attempt requires a new key.
- Treat `oc` failures as connectivity, RBAC, or cluster-availability errors.
- Do not reuse output directories for commands that may produce the same deterministic UUIDs unless duplicate-record errors are expected.
- Do not add an external model provider without an explicit data-flow and approval review.
