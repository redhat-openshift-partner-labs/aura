# Implemented Architecture

## Position

Aura is a read-only sidecar control plane. Existing systems remain authoritative:

| Data | Authority |
| --- | --- |
| Desired cluster definition | `fleet-clusters` Git commit at `HEAD` |
| Actual cluster state | OpenShift |
| Workflow execution | Tekton |
| Operator request data | Portal, when eventually integrated |
| Aura commands, evidence, CWA traces, and audit records | Aura |

Aura freezes observations, correlates them, and explains them.

## Flow

```mermaid
flowchart TB
  O[Operator CLI] --> C[Command Envelope]
  C --> A[Authorization Policy]
  A --> S[Evidence Service]
  S --> G[Git Inventory Collector]
  S --> K[OpenShift Inventory Collector]
  S --> T[Tekton Inventory Collector]
  G --> E[Frozen Evidence Snapshot]
  K --> E
  T --> E
  E --> W[CWA Assembler]
  W --> M[Deterministic Diagnosis Model]
  M --> R[Strict Diagnosis Response]
  E --> ES[(Evidence Store)]
  W --> CS[(CWA Snapshot Store)]
  W --> CT[(CWA Trace Store)]
  R --> DS[(Diagnosis Store)]
  E --> AE[(Audit Store)]
  W --> AE
  R --> AE
```

## Layers

| Layer | Responsibility |
| --- | --- |
| CLI | Parses arguments, validates flag combinations, wires collectors, prints JSON |
| Services | Enforce command type, state transition, and operator authorization |
| Collectors | Read external systems and produce typed evidence items |
| Correlators | Combine source evidence into cluster, workflow, and diagnosis views |
| CWA | Freeze model input into nine required slots and compute digests |
| Diagnosis | Validate strict model output and link it to CWA provenance |
| Stores | Persist immutable JSON records with atomic exclusive publication |
| Audit | Record evidence collection, context assembly, and model results |

## Read-Only Boundary

Phase 1 performs only these external reads:

| Source | Read |
| --- | --- |
| Git | Committed definition files and the `deprovision/` listing from `HEAD` |
| OpenShift | `clusterdeployments` and `managedclusters` |
| Tekton | `pipelineruns` and `taskruns` |

Aura does not:

- Write Git files.
- Create, update, patch, or delete Kubernetes resources.
- Start or cancel PipelineRuns.
- Write Portal data.
- Publish messages to RabbitMQ.
- Send cluster evidence to an external model provider.

## Evidence and Provenance

Every collected result is a typed `EvidenceSnapshot`. It contains:

- Snapshot and command IDs.
- Collector name and version.
- Collection timestamp.
- Source name and version.
- Typed evidence items.
- Redaction list.
- SHA-256 snapshot digest.

The CWA assembler links an evidence snapshot to a CWA snapshot and trace. A diagnosis links to the CWA snapshot and includes a response digest. Audit events connect the command to all of those records.

## Model Boundary

The current diagnosis model is deterministic and local. No external provider is configured.

The provider-independent contract is:

1. Receive a `CWASnapshot`.
2. Return a strict `DiagnosisResponse`.
3. Never mutate evidence.
4. Never bypass authorization.
5. Never execute an action.

Model output is untrusted data. Pydantic validates the exact response shape, confidence bounds, evidence references, and digests.

## Refusal

`diagnose state` refuses to answer if required evidence is missing:

| Missing evidence | Refusal message component |
| --- | --- |
| Git definition | `Git cluster definition` |
| Git required files, unless deprovision is requested | `Git cluster definition files` |
| Interpretable Git definition, unless deprovision is requested | `Valid Git cluster definition` |
| Single Git definition | `Conflicting Git cluster definitions` |
| Selected PipelineRun | `Selected Tekton PipelineRun NAME_OR_UID` |
| OpenShift `Provisioned` condition | `OpenShift Provisioned condition` |
| OpenShift actual state, unless an active definition has a deprovision sentinel | `OpenShift actual state` |
| ManagedCluster status | `ManagedCluster status` |
| Tekton run, unless deprovision is requested | `Tekton PipelineRun status` |

The command exits with code `2`. Evidence, CWA snapshot, CWA trace, and audit records are still preserved.

## Proposals

`propose deprovision` turns the same cluster-state evidence into a deterministic, digest-verified `Proposal` that adds `deprovision/<id>-archive`. Aura stores the proposal and never applies it, commits it, or pushes it. Unmet preconditions refuse the command with every reason, and the evidence, CWA, and audit records are preserved.

## Execution Store

Phase 3 commands keep their records in the execution store in `aura.execution.store` ([D13](plans/06-phase-3-approved-execution.md#execution-store)). Its connection comes only from Aura's configuration file, `aura.config`: TOML at `$XDG_CONFIG_HOME/aura/config.toml`, default `~/.config/aura/config.toml`, or the file the `AURA_CONFIG` environment variable names. A missing, unreadable, or invalid file, or a blank `AURA_CONFIG` or `XDG_CONFIG_HOME`, is an error.

```toml
[store]
backend = "sqlite"
path = "/absolute/path/aura.db"
login = "<operator>"

[execution]
adapters = []
adapter_timeout_seconds = 300

[git]
author_name = "<name>"
author_email = "<email>"
```

A `postgresql` store takes `host`, `port` (default `5432`), `database`, `user`, and an absolute `password_file` instead of `path` and `login`. `adapters` lists enabled adapters from `git_handoff`, `git_pr`, `tekton`, and `recording`; `[execution]` and `[git]` are optional.

The store only inserts rows. Each row holds its record's canonical JSON as text, its digest, copies of the fields that constraints and queries use, and `stored_at` from the database. Load verifies the record's own digest and that the digest column and every copied column equal the record's fields. A signed record, an approval, is also verified against the hub's allowed-signers list at the row's `stored_at`, so a key retired later still verifies it; a reader without the list cannot read signed rows. An identical insert under a stored key succeeds; a different record under it is a conflict. When a write's outcome is unknown, the store reads the row back by its key: an identical row counts as stored, an absent row is a failed write, and an unreadable store raises an error that names the key. A locked check-and-insert transaction lets two writers that check the same condition commit at most once. Idempotency keys are unique per login, and each login reads only its own commands.

The schema is versioned by migration files in `aura/execution/migrations/` that only add tables, columns, and indexes. A client refuses a store whose highest version is outside the range it supports. A new SQLite file applies the migrations and records a deployment ID when it is created, for development; in production the database administrator applies them. Phase 3 Stage 2 adds the commands that use the store.

## Failure Model

| Failure | Current behavior |
| --- | --- |
| Missing flag | CLI error, exit code `2` |
| Unauthorized role | Service error, exit code `2` |
| Missing diagnosis evidence | Refusal, exit code `2`, records preserved |
| Unmet proposal precondition | Refusal, exit code `2`, no proposal stored, other records preserved |
| Duplicate immutable record | Store error, exit code `2` |
| External `oc` failure | Converted to `OpenShiftCLIError`; CLI reports a concise error and exits `2` without partial JSON |
| Model unavailable | Generic diagnosis service falls back deterministically |
| Terminal idempotency key reused | CLI exits `2` before evidence collection and creates no new records |
| Idempotency key reused for a different request | CLI exits `2` before evidence collection |
| Record write failure | Command stored as `failed` when possible; CLI exits `2` with the operating-system error |
| Unreadable idempotency-key record | CLI exits `2` before evidence collection |
| Unhandled error after the idempotency check | CLI prints `aura: internal error`, or `aura: filesystem error` for an `OSError`, stores a `failed` record unless a terminal record is already stored or a `refused` record when the chain already ends with `command_refused`, and exits `2` |
| Invalid Git definition | Emitted as `cluster_definition_invalid`; other definitions are still collected |

Command records are written only in a terminal state. `succeeded`, `failed`, and `refused` records are immutable; a new attempt requires a new idempotency key.
