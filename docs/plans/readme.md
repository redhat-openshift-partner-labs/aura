# Aura Planning Index

## Status

Phase 1 is implemented, validated, and published. The [Phase 2](05-phase-2-proposals.md) plan is approved; all four proposal types are implemented and validated against the live hub, and [Phase 2 Acceptance](09-phase-2-acceptance.md) grades every criterion met or met with caveat, pending operator sign-off. [Phase 3](06-phase-3-approved-execution.md) is a draft aligned with Phase 2 and awaits approval.

## Objective

Build Aura as a clean, operator-facing control plane for OpenShift Partner Labs without modifying the four protected repositories:

- `fleet`
- `fleet-cluster-scaffold`
- `fleet-clusters`
- `portal`

Aura will help a human operator understand current state, diagnose problems, prepare changes, and eventually execute approved actions. It will not replace Tekton, Argo CD, ACM/Hive, Git, or OpenShift as the authority for cluster lifecycle.

## Decision Summary

| Decision | Position |
| --- | --- |
| Primary architecture | Sidecar control plane, not a replacement workflow engine |
| Existing queue system | Leave in place; do not require it for Aura |
| Cluster workflow engine | Continue using Tekton through `fleet` |
| Source of truth | Git for desired state, OpenShift for actual state, Aura only for commands and audit |
| Portal integration | Read-only or none; Aura does not write Portal's database |
| Model integration | Every model call is assembled through Context Window Architecture (CWA) |
| Phase 2 generation | Deterministic only; no model writes proposals |
| First release | Read-only diagnosis and inventory |
| Autonomous execution | Deferred until guarded, approved execution is proven |

## Plan Documents

| Document | Purpose |
| --- | --- |
| [Current State](01-current-state.md) | Constraints, pain points, and why a sidecar rebuild is justified |
| [Target Architecture](02-target-architecture.md) | Components, boundaries, state ownership, and CWA integration |
| [Phase 0: Baseline](03-phase-0-baseline.md) | Contracts, access, and evidence model before product work |
| [Phase 1: Read-Only Assistant](04-phase-1-read-only-assistant.md) | Inventory and diagnosis without side effects |
| [Phase 2: Proposals](05-phase-2-proposals.md) | Patch, pipeline, and action proposals without execution |
| [Phase 2 Acceptance](09-phase-2-acceptance.md) | Criterion-by-criterion evidence and grades for Phase 2 sign-off |
| [Phases 0–2 Closure](10-phase-0-2-closure.md) | Diagnosis, closure rules, criteria, and the staged plan to close Phases 0–2 |
| [Phases 0 and 1 Acceptance](11-phase-0-1-acceptance.md) | Criterion-by-criterion evidence and grades for Phase 0 and Phase 1 sign-off |
| [Phase 3: Approved Execution](06-phase-3-approved-execution.md) | Guarded execution after explicit human approval |
| [Testing, Security, and Rollout](07-testing-security-rollout.md) | Validation strategy and release gates |
| [Open Questions](08-open-questions.md) | Decisions needed before implementation |

## High-Level Flow

```mermaid
flowchart LR
  O[Operator] --> A[Aura]
  A --> E[Evidence Collection]
  E --> C[CWA Assembler]
  C --> M[Model]
  M --> G[Guard and Validation]
  G --> H[Human Approval]
  H --> X[Execution Adapters]
  X --> S[fleet-cluster-scaffold]
  X --> F[fleet-clusters]
  X --> T[OpenShift / Tekton]
  A --> D[(Aura Audit Database)]
```

## Non-Negotiable Boundaries

1. Do not modify code in `fleet`, `fleet-cluster-scaffold`, `fleet-clusters`, or `portal`.
2. Do not write Portal's database.
3. Do not expose secrets to the model.
4. Do not let model output become authoritative state.
5. Do not execute a command without schema validation, policy validation, and explicit approval.
6. Do not replace Tekton as the cluster workflow engine.

