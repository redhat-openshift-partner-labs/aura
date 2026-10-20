# Aura Control Plane

Aura is a read-only operator control plane for OpenShift Partner Labs. It correlates committed `fleet-clusters` Git definitions, OpenShift actual state, and Tekton workflow state into typed, auditable evidence. Phase 1 functional scope and closure hardening are implemented and validated.

Aura is a sidecar to the existing lifecycle systems. It does not replace RabbitMQ, Tekton, Git, ACM/Hive, or Portal, and it does not write to those systems.

## Current Capabilities

- List active and archived cluster definitions from `fleet-clusters`.
- Read OpenShift `ClusterDeployment` and `ManagedCluster` state.
- Read Tekton `PipelineRun` and `TaskRun` status.
- Correlate Git, OpenShift, and Tekton evidence by cluster name.
- Derive cluster inventory and workflow views.
- Diagnose one cluster's state with deterministic, schema-valid output.
- Refuse diagnosis when required evidence is missing.
- Freeze evidence and CWA snapshots with SHA-256 digests.
- Store immutable JSON evidence, CWA, audit, and diagnosis records.

## Non-Goals

- Patch generation and pull requests.
- Pipeline execution.
- Deprovisioning.
- Portal writes.
- RabbitMQ replacement.
- Autonomous execution.
- A long-running service or web UI.

## Quick Start

Prerequisites:

- Python 3.12 or newer.
- `uv`.
- `oc`.
- A local `fleet-clusters` checkout.
- A kubeconfig for the read-only `aura:aura-readonly` service account.

Run the test suite:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
```

List Git cluster definitions:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source git \
  --fleet-clusters ../fleet-clusters \
  --operator operator@example.com \
  --idempotency-key inventory-001 \
  --command-dir ./local/commands
```

List combined live inventory:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura inventory list \
  --source combined \
  --fleet-clusters ../fleet-clusters \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --operator operator@example.com \
  --idempotency-key inventory-002 \
  --command-dir ./local/commands
```

Inspect workflows:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync aura workflow inspect \
  --openshift-kubeconfig "$HOME/.kube/aura-readonly" \
  --fleet-clusters ../fleet-clusters \
  --operator operator@example.com \
  --idempotency-key workflow-001 \
  --command-dir ./local/commands
```

See [`docs/cli.md`](docs/cli.md) for every flag, default, and required value.

## Documentation

| Document | Purpose |
| --- | --- |
| [`docs/status.md`](docs/status.md) | Implemented capabilities and remaining gaps |
| [`docs/architecture.md`](docs/architecture.md) | Implemented architecture and boundaries |
| [`docs/wiring.md`](docs/wiring.md) | CLI, service, collector, and store wiring |
| [`docs/cli.md`](docs/cli.md) | Command and flag reference |
| [`docs/evidence.md`](docs/evidence.md) | Evidence, CWA, audit, and diagnosis records |
| [`docs/security.md`](docs/security.md) | Read-only access, RBAC, and secret handling |
| [`docs/validation.md`](docs/validation.md) | Test, fixture, and live validation procedures |
| [`docs/plans/readme.md`](docs/plans/readme.md) | Original planning documents |

Agent guidance is in [`AGENTS.md`](AGENTS.md). Claude-specific guidance is in [`CLAUDE.md`](CLAUDE.md).

## Repository Layout

| Path | Purpose |
| --- | --- |
| `src/aura/cli.py` | Argument parsing and command wiring |
| `src/aura/collectors/` | Git, OpenShift, Tekton, and correlation collectors |
| `src/aura/services/` | Authorization-aware command services |
| `src/aura/contracts.py` | Typed command and evidence contracts |
| `src/aura/cwa.py` | CWA snapshot and trace contracts |
| `src/aura/diagnosis.py` | Diagnosis response and record contracts |
| `src/aura/*_store.py` | Append-only JSON stores |
| `tests/` | Contract, collector, service, CLI, and fixture tests |
| `docs/plans/` | Architecture and phased implementation plans |

## Protected Repositories

Do not modify:

- `fleet`
- `fleet-cluster-scaffold`
- `fleet-clusters`
- `portal`

Aura reads `fleet-clusters` and OpenShift only. The other protected repositories are not required by the current Phase 1 runtime.
