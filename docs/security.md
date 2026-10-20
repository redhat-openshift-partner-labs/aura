# Security and Access

## Security Model

Phase 1 is read-only:

| System | Access | Writes |
| --- | --- | --- |
| `fleet-clusters` | Local working tree | None |
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

Git evidence is read from the committed `HEAD` tree. Uncommitted working-tree changes and untracked files are not treated as desired state.

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

The service account needs only `get` and `list` on four resources:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: aura-readonly
rules:
- apiGroups: ["hive.openshift.io"]
  resources: ["clusterdeployments"]
  verbs: ["get", "list"]
- apiGroups: ["cluster.open-cluster-management.io"]
  resources: ["managedclusters"]
  verbs: ["get", "list"]
- apiGroups: ["tekton.dev"]
  resources: ["pipelineruns", "taskruns"]
  verbs: ["get", "list"]
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
```

Verify authorization:

```bash
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list clusterdeployments.hive.openshift.io -A
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list managedclusters.cluster.open-cluster-management.io
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list pipelineruns.tekton.dev -A
oc --kubeconfig "$HOME/.kube/aura-readonly" auth can-i list taskruns.tekton.dev -A
```

Every command must print `yes`.

## Secret Redaction

`RedactionPolicy` removes values for these field names:

- `api_key`
- `authorization`
- `client_secret`
- `cookie`
- `kubeconfig`
- `password`
- `private_key`
- `pull_secret`
- `secret`
- `ssh_private_key`
- `token`

Matching is case-insensitive and recursive through mappings and lists. Removed field paths are recorded without their values.

Redaction is a defense in depth. Collectors should still avoid reading secret values in the first place.

## Model Boundary

Every future model call must:

1. Consume a frozen CWA snapshot.
2. Return strict `DiagnosisResponse` JSON.
3. Be validated by deterministic code.
4. Remain unauthorized to execute anything.

Model output is never treated as authority. Authorization and any future execution guards remain deterministic code outside the model.

## Record Integrity

All current stores use exclusive file creation:

- Existing snapshots are not overwritten.
- Existing audit events are not overwritten.
- Existing completed-command records are not overwritten.
- Existing CWA records are not overwritten.
- Existing diagnoses are not overwritten.

Snapshots and responses include SHA-256 digests over canonical JSON.

## Operational cautions

- Use one idempotency key per intended command; completed keys are suppressed through `--command-dir`.
- Treat `oc` failures as connectivity, RBAC, or cluster-availability errors.
- Do not reuse output directories for commands that may produce the same deterministic UUIDs unless duplicate-record errors are expected.
- Do not add an external model provider without an explicit data-flow and approval review.
