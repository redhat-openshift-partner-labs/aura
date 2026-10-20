# Phase 1: Read-Only Assistant

## Status

Complete and revalidated on 2026-10-19 through implementation commit `97c6ece`.

## Objective

Deliver the first useful Aura capability: explain current partner-labs state without side effects.

Phase 1 does not generate patches, start pipelines, or modify any repository.

## User Stories

1. As an operator, I can list active and archived cluster definitions.
2. As an operator, I can view a cluster's desired state from Git.
3. As an operator, I can view a cluster's actual state from OpenShift.
4. As an operator, I can inspect recent Tekton PipelineRuns.
5. As an operator, I can ask why a cluster appears stuck.
6. As an operator, I can see the evidence and CWA trace behind an answer.

## Required Views

### Cluster Inventory

| Field | Source |
| --- | --- |
| Cluster name | `fleet-clusters` |
| Tier | `fleet-clusters` |
| Cloud provider | `fleet-clusters` |
| Region | `fleet-clusters` |
| Desired state | Git |
| Actual state | OpenShift |
| Provisioning status | OpenShift |
| Latest pipeline run | Tekton |
| Drift indicator | Derived |

### Workflow View

| Field | Source |
| --- | --- |
| PipelineRun name | Tekton |
| Pipeline name | Tekton |
| Status | Tekton |
| Start and completion time | Tekton |
| Failed task | Tekton |
| Related cluster | Git and OpenShift |
| Next diagnostic step | Model proposal |

### Diagnosis View

The diagnosis must:

- Separate facts from inference.
- Cite the evidence used.
- Identify missing evidence.
- Explain likely causes.
- Propose a safe next inspection step.
- Avoid claiming certainty without evidence.

## Evidence Collection Flow

```mermaid
sequenceDiagram
  participant O as Operator
  participant A as Aura
  participant G as Git
  participant K as OpenShift
  participant T as Tekton
  participant C as CWA
  participant M as Model

  O->>A: Ask about cluster state
  A->>G: Read cluster definitions
  A->>K: Read ClusterDeployment and ManagedCluster
  A->>T: Read PipelineRun status
  A->>C: Freeze evidence snapshot
  C->>M: Assemble model payload
  M-->>A: Structured diagnosis
  A-->>O: Answer, evidence, and trace link
```

## Output Contract

The model must return strict JSON. The first response schema should contain:

| Field | Purpose |
| --- | --- |
| `summary` | Short operator-facing answer |
| `facts` | Deterministic observations used |
| `inferences` | Model explanations, marked as inference |
| `missing_evidence` | Data required for a stronger diagnosis |
| `recommended_inspection` | Read-only next step |
| `confidence` | Model confidence |
| `evidence_ids` | References to collector items |

No execution command is allowed in Phase 1.

## Acceptance Criteria

- [x] Aura lists cluster definitions without writing to Git.
- [x] Aura lists OpenShift resources without write permissions.
- [x] Aura correlates Git, OpenShift, and Tekton data by cluster name.
- [x] Aura stores the frozen evidence snapshot.
- [x] Aura stores the CWA trace and snapshot digest.
- [x] Aura refuses to answer when required evidence is missing.
- [x] Aura returns schema-valid JSON from every model call.
- [x] Aura provides a deterministic fallback answer when the model is unavailable.
- [x] Aura suppresses completed idempotency keys before evidence collection.
- [x] Aura reports OpenShift CLI failures concisely without partial JSON.

## Validation Record

| Check | Result |
| --- | --- |
| Automated tests | `199 passed` with `uv run --no-sync pytest` |
| Diff hygiene | `git diff --check` passed |
| Quality gate | `ripwire . --quality-delta` reported zero regressions |
| Scoped-AWS fixture | Validated `provision/0171d46a-scopedaws` at `fleet-clusters` commit `492bc8e07afddcd557441ddb7d72158de60ab8cb` |
| Git inventory | Reported active and archived definitions, including incomplete definitions with status |
| Live OpenShift | Read-only service account listed 13 cluster states |
| Live Tekton | Read-only service account listed 34 PipelineRuns |
| Combined inventory | Correlated 16 cluster inventory records across Git, OpenShift, and Tekton |
| Workflow view | Correlated all 34 PipelineRuns to their `cluster-name` parameter |
| Immutable records | Stored one evidence snapshot, one CWA snapshot, one CWA trace, and linked audit events with matching digests |
| Model contract | Automated tests validate strict JSON responses, deterministic fallback, and refusal when required evidence is missing |
| Live diagnosis | Diagnosed `0171d46a-scopedaws` with Git, OpenShift, Tekton, and correlation evidence |
| Non-provisioned refusal | Refused diagnosis for `1bd62dc5-hpezerto` with structured missing evidence and no diagnosis record |
| Duplicate idempotency key | Rejected before collection with no new command record |
| Protected repositories | `fleet`, `fleet-cluster-scaffold`, `fleet-clusters`, and `portal` had no tracked modifications |
| Post-audit fixes | Commits `7debde5`, `447f748`, and `97c6ece` added conflict indicators, incomplete-definition handling, explicit PipelineRun selection, structured non-provisioned diagnosis, concise `oc` errors, and completed-key suppression |

## Out of Scope

- Patch generation.
- PR creation.
- Pipeline execution.
- Deprovisioning.
- Portal writes.
- Legacy queue integration.

