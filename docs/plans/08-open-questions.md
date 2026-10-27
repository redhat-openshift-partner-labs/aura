# Open Questions

## Repository Boundaries

### Does “do not change `fleet-clusters`” mean no code changes or no content changes?

Options:

| Option | Meaning |
| --- | --- |
| Code freeze | Aura may create operational branches and PRs |
| Full freeze | Aura may only generate patches for manual application |

Default assumption: full freeze until explicitly changed.

### Should Aura create pull requests?

If yes, define:

- Allowed base branches.
- Required reviewers.
- Allowed file paths.
- Forbidden paths.
- Commit message format.
- Whether Aura may merge.

Default: Aura may propose a PR but never merge it.

Status: pending as decisions D8 (execution path) and D12 (merge method) in the [Phase 3 plan](06-phase-3-approved-execution.md#operator-decisions).

## Fleet-Clusters Data

### Backfill `metadata.yaml` for definitions that predate it

Status: resolved on 2026-10-25. On 2026-10-23 the operator chose to keep `metadata.yaml` required and to build `cluster_edit` backfill proposals that write all four keys under `provision/` only; see [Existing-Cluster Edits](05-phase-2-proposals.md#existing-cluster-edits). The operator then backfilled `metadata.yaml` under both `provision/` and `archive/` in `fleet-clusters` commit `3cf829d`, pushed with `[skip-provision]` in its message. That commit added 14 files and appended keys to 13, leaving every `metadata.yaml` with all four `fleet` keys. With `metadata.yaml` present, one archived definition then reported as `cluster_definition_invalid`: its ClusterDeployment patch lacked `/spec/provisioning/imageSetRef/name`, which the missing file had masked. The operator added the path in `a8a6bf0`. At `a8a6bf0`, Git inventory reports 27 complete definitions, none incomplete or invalid; see [Current Git Inventory](../validation.md#current-git-inventory).

The history below records the state before the backfill.

`metadata.yaml` is a required definition file. At `fleet-clusters` commit `492bc8e07afddcd557441ddb7d72158de60ab8cb`, 14 of 26 committed definitions do not contain it because they were created before the file was introduced:

| Directory | Definitions |
| --- | --- |
| `provision/` | 11 definitions |
| `archive/` | 3 definitions |

Aura reports these as `cluster_definition_incomplete` items with `missing_files: ["metadata.yaml"]`, includes them in combined inventory, and refuses diagnosis with `Git cluster definition files`. Aura does not modify `fleet-clusters`; resolution is a backfill by the repository owners.

Warning: adding `metadata.yaml` adds a file under `provision/<id>`, which the `fleet` EventListener treats as a pre-provision trigger. Push the backfill with `[skip-provision]` in the push head commit message; the trigger checks only the head commit. See [Phase 2 lifecycle facts](05-phase-2-proposals.md#lifecycle-facts).

### Backfill `platform` in legacy `metadata.yaml` files

Status: resolved on 2026-10-25. On 2026-10-23 the operator chose to build `cluster_edit` proposals that add every missing key to these files, under `provision/` only. The same `3cf829d` backfill added `platform` and the other missing keys; at `a8a6bf0`, every definition has a non-null `cloud_provider`.

At the same commit, 11 of the 12 complete definitions have a `metadata.yaml` without `platform`; only one, under `provision/`, declares it:

| Directory | Definitions |
| --- | --- |
| `provision/` | 7 definitions |
| `archive/` | 4 definitions, one of which also has an active definition under `provision/` |

Aura keeps the desired `cloud_provider` as `null` rather than inferring it. When every other compared field matches OpenShift, combined inventory reports `drift_indicator: incomplete-metadata` instead of `detected`.

Editing an existing `metadata.yaml` modifies a file and triggers nothing, so `[skip-provision]` is not required for that change alone.

### Stranded deprovision sentinels

Status: open, owned by `fleet-clusters` maintainers; deferred from Phases 0–2 (D5) and tracked in the [closure plan backlog](10-phase-0-2-closure.md#backlog).

The `archive-cluster` Action requires `deprovision/<id>-archive`, but at the validated commit seven sentinels lack the suffix. Six of those clusters remain under `provision/` with no OpenShift resources, and Aura reports them as `deprovision_status: stranded`; the seventh is already archived and reports `leftover`. Only two still have a retained, succeeded deprovision PipelineRun; the other four have no PipelineRun on the hub, so whether their deprovision ran cannot be confirmed from Tekton. The `fleet-clusters` README documents the bare `deprovision/<id>` form. Remediation is undecided because re-adding a `-archive` sentinel re-runs deprovision. Any remediation sentinel must be a standalone commit that adds only `deprovision/<id>-archive`, with a commit message that states it remediates a stranded deprovision; Aura never creates or pushes it.

## Operator Interface

### Which interface should ship first?

Options:

| Interface | Benefits | Drawbacks |
| --- | --- | --- |
| CLI | Fast, auditable, easy to test | Less approachable |
| Chat | Good for diagnosis and operator workflow | Harder approval UX |
| Web UI | Best long-term operator surface | More implementation work |

Default: CLI first, chat second, web UI later.

## Identity and Authorization

Needed decisions:

1. Which identity provider should Aura use?
2. How are operator roles mapped?
3. Can the proposer and approver be the same person in emergencies?
4. How are approvals recorded and revoked?
5. Should approvals expire?

Default: start with local CLI identity and explicit role configuration.

Status: pending as decisions D7 (identity) and D11 (approval policy, emergencies, expiry, revocation) in the [Phase 3 plan](06-phase-3-approved-execution.md#operator-decisions).

## Portal Integration

### Should Aura read Portal's database?

Options:

| Option | Tradeoff |
| --- | --- |
| Read-only | Useful metadata, but schema coupling |
| No integration | Clean boundary, less operator context |

Default: no Portal integration in Phase 1.

## Model Provider

Needed decisions:

1. Hosted or local model provider?
2. Required context window and output token budget.
3. Latency and cost limits.
4. Data-retention requirements.
5. Fallback behavior when the model is unavailable.

Default: model provider is pluggable and never referenced by business logic.

## CWA Implementation

Needed decisions:

1. Which conformant assembler implementation should Aura use?
2. Where are route profiles stored?
3. Who approves profile changes?
4. Which tokenizer is used for the selected model?
5. How are CWA traces retained?

Default: use a conformant assembler behind a narrow internal interface.

## OpenShift Access

Needed decisions:

1. Which namespace holds Aura?
2. Which ServiceAccount reads inventory?
3. Which ServiceAccount starts Tekton runs?
4. Which resources are readable?
5. Are logs fetched directly from Tekton or through a log store?

Default: separate read-only and execution ServiceAccounts.

## Legacy System

### Should Aura eventually consume the legacy queue or worker state?

Options:

| Option | Tradeoff |
| --- | --- |
| Ignore legacy path | Simplest and safest |
| Read-only integration | Better history, more coupling |
| Full migration | High cost and risk |

Default: ignore the legacy path until Aura's core value is proven.

