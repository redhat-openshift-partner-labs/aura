# Phases 0–2 Closure Plan

## Status

Approved 2026-10-25 at Aura commit `3b73a3f`; the operator accepted D1–D6 as recommended. Stage 0 is complete: the decisions are applied to the governing plans, deferred items live only in the [Backlog](#backlog), and current verification results live only in [Validation](../validation.md). Stage 1 is complete: its review found three invariant or regression findings, fixed in `2f0bfb8` (F1), `976270c` (F3), and `f617dbe` (F2, operator option A), with its other notes in the [Backlog](#backlog); the `act` result is in [Validation](../validation.md). Stage 2 is complete: its review found three Phase 0 rows whose evidence did not fail when the rule broke, corrected in `0d85f4c` and `9c3fefc`. **Next: Stage 3 review and `act`; task 4.5 awaits operator approval.** Mark each task done in [Progress](#progress) in the same commit series that completes it.

## Why This Plan Exists

Every fix round from the scaffold work onward found new issues. Most were latent defects a wider review newly looked at, several were introduced by fixes, and every round left docs stale. The causes:

1. No frozen definition of done, and open-ended reviews, so scope grew each round.
2. Defect classes fixed one instance at a time. The CLI has no exception boundary after the idempotency check, so any exception a route does not list prints a traceback and stores no `failed` record. Seven `subprocess.run` sites use three isolation styles.
3. No structural test enforcing cross-cutting rules.
4. Volatile facts restated across many documents.
5. The quality gate run before committing, which analyzes the previous commit.

## Rules

| Rule | Statement |
| --- | --- |
| R1 Frozen scope | Closure means the [Closure Criteria](#closure-criteria) and nothing else. Only an operator decision recorded here changes them. |
| R2 Triage | A finding blocks closure only if it violates a closure criterion or a project invariant (I1–I9 in [Development](../development.md#project-invariants)). Anything else is added to the [Backlog](#backlog) with a target, and is not fixed now. |
| R3 Classes at chokepoints | A defect of a class is fixed at one chokepoint, with a structural test that fails when any code path bypasses it. |
| R4 No regression by editing tests | An existing test may change only when the task says so. Any other edit to an existing test is a regression signal: stop, explain it in the commit message, and ask if unsure. |
| R5 Verification order | Focused tests, full suite, `git diff --check`, commit, then `ripwire . --quality-delta=adb2c2b..HEAD` with `target_ref` equal to the new commit. Fix gate findings in a follow-up commit before the next task. Run `act` at the end of each stage. |
| R6 One source per fact | Current test counts and `act` results live only in [Validation](../validation.md); other documents link to it. |
| R7 Bounded review | One independent review per stage, using the [review brief](#review-brief). It reports only criterion or invariant violations and regressions. |
| R8 Stop rule | A phase is closed when every criterion is met or met with an operator-accepted caveat, and the Stage 5 review reports no violation. |

## Session Guardrails

Before any task:

1. Read [AGENTS.md](../../AGENTS.md), [Development](../development.md), and this plan.
2. `git status` is clean, the full suite passes at `HEAD`, and the gate at `HEAD` reports zero gating findings. If not, stop and report; do not fix unrelated failures.

Always:

- Never modify `fleet`, `fleet-cluster-scaffold`, `fleet-clusters`, or `portal`. Read sibling checkouts only through Aura's code or read-only Git commands.
- Never push. Commit with `git commit -s` and no AI attribution trailer; the global hooks reject both a missing sign-off and such a trailer.
- Never write real cluster names in docs or commit messages; use `<cluster-name>`.
- Live hub or local-scaffold runs need the operator's approval each time, use `~/.kube/aura-readonly`, and first confirm `oc whoami` is `system:serviceaccount:aura:aura-readonly` and writes are denied.

Stop and ask the operator when the spec is silent or contradictory, when a fix needs a contract or schema change this plan does not list, or when a new test cannot be made to fail before its fix.

## Closure Criteria

| ID | Criterion | Source |
| --- | --- | --- |
| C0.1–C0.6 | Phase 0 acceptance criteria | [Phase 0](03-phase-0-baseline.md#acceptance-criteria) |
| C0.7 | Phase 0 deliverables exist or are superseded (D4) | [Phase 0](03-phase-0-baseline.md#deliverables) |
| C1.1–C1.16 | Phase 1 acceptance criteria, proved at the closure commit | [Phase 1](04-phase-1-read-only-assistant.md#acceptance-criteria) |
| C1.17 | Phase 1 live validation re-run at the closure commit | [Validation](../validation.md) |
| C2.1–C2.18 | A1–A13 and F1–F5, each met or with an operator-accepted caveat | [Phase 2 Acceptance](09-phase-2-acceptance.md) |
| C2.19 | Generator test table, including one trigger-effect test per lifecycle fact | [Testing plan](07-testing-security-rollout.md#proposal-generator-tests) |
| C2.20 | Live validation of all four proposal types at the closure commit | [Validation](../validation.md) |
| CX.1 | I1–I9 hold for every command, proved by the shared failure harness | [Development](../development.md#project-invariants) |
| CX.2 | Security requirements 1–10 as amended by D3 | [Testing plan](07-testing-security-rollout.md#security-requirements) |
| CX.3 | No protected repository modified | [AGENTS.md](../../AGENTS.md) |

## Stage 1: Fix Classes at Chokepoints

Order: 1.1, 1.2, 1.3, 1.4. Existing tests must pass unchanged (R4) unless a task says otherwise.

### 1.1 CLI exception boundary

- **Where:** `src/aura/cli.py`, `_execute_command`, around `return action()` inside `store.lock()`. Catch `Exception` only; `KeyboardInterrupt` and `SystemExit` pass through.
- **Behavior:** call a new `_fail_unexpected(args, command, error, clock)` that:
  - prints `aura: internal error: <ExceptionType>: <message>` with no traceback and returns `2`;
  - does nothing more if a terminal record for the idempotency key is already stored;
  - otherwise reuses `_fail_command` with that message, so the `failed` record and `command_failed` event follow the existing path. `RECEIVED` may transition to `FAILED`.
  - passes as `previous_event_id` the last stored audit event of this command: load `--audit-dir` with `JsonAuditEventStore`, keep events with this `command_id`, and take the one no other event names as `previous_event_id`; `None` when there is no audit directory or no event.
- **Tests first:**
  - In `tests/test_failure_matrix.py`, for every scenario, inject `RuntimeError`, `RecursionError`, and `UnicodeDecodeError` from the collector (extend `FailingReadCollector`). Expect exit `2`, standard error `aura: internal error: <type>: injected read failure`, no traceback, a `failed` record, the chain `command_received` then `command_failed` when audited, and `assert_command_invariants` passing.
  - For each proposal scenario and `diagnose`, monkeypatch the generator or diagnosis model to raise `RuntimeError`. Expect the chain to end `evidence_collected` then `command_failed`.
- **Done when:** the new rows pass, every existing test passes unchanged, and the gate is clean.

### 1.2 Process chokepoint

- **New module** `src/aura/process.py`:
  - `run_process(argv, *, env, timeout, cwd=None, input=None) -> ProcessResult`, where `ProcessResult` holds `returncode`, `stdout: bytes`, and `stderr: bytes`, with `stdout_text()` and `stderr_text()` that decode UTF-8 with replacement.
  - Behavior: never `check=True`; `stdin` is `DEVNULL` unless `input` is given; `env` is required (pass `None` explicitly to inherit); raises `ProcessError` when the program cannot start or times out.
- **Migrate the seven call sites.** Each caller keeps raising its own error type by mapping `ProcessError` and non-zero exits:

  | Site | Environment | Error type |
  | --- | --- | --- |
  | `collectors/oc_cli.py` `_run_command` | inherited | `OpenShiftCLIError`; non-UTF-8 output is an error, not replaced |
  | `collectors/git_inventory.py` `_run_git` | `isolated_git_environment()` | `GitInventoryError` |
  | `collectors/git_staleness.py` `_history_error` | `isolated_git_environment()` | `GitInventoryError` |
  | `collectors/scaffold_evidence.py` `_git` | `isolated_git_environment()` | `ScaffoldCheckoutError` |
  | `scaffold_runner.py` (three sites: scaffold, `kustomize version`, Git) | minimal for the scaffold; inherited for `kustomize`; isolated for Git | `ScaffoldRunError` |

- **Structural test:** `tests/test_architecture.py` parses every module under `src/aura` with `ast` and fails if any module other than `aura/process.py` imports `subprocess`.
- **Tests first:** unit tests for `run_process` covering no stdin, timeout, missing program, bytes, and replacement decoding, plus an `oc` client test that non-UTF-8 output raises `OpenShiftCLIError`.
- **Done when:** the structural test passes, all existing tests pass unchanged, and the gate is clean.

### 1.3 Call budget for the hub (D3)

- **What counts:** `oc` invocations only, since they are the hub calls. Local Git, `kustomize`, and scaffold runs are bounded by their timeouts.
- **Where:** `collectors/oc_cli.py` `_read_oc`, so it counts calls through injected fake runners too. The budget is a `contextvars.ContextVar` that `cli._execute_command` sets per command, at 10 calls; the most any command makes today is 5.
- **Behavior:** the eleventh call raises `OpenShiftCLIError("oc call budget of 10 exceeded")`, which fails the command through the existing route errors.
- **Tests first:** a fake runner that counts calls. Ten calls succeed, the eleventh fails with a `failed` record, and the budget resets between commands.

### 1.4 Cluster name rule (D6)

- **Contract:** in `src/aura/proposals.py`, `Proposal` rejects a `cluster_name` containing `-archive`: `cluster_name must not contain -archive`.
- **CLI:** `_propose_argument_error` rejects `--cluster` values containing `-archive` for every `propose` subcommand, before collection.
- **Scaffold:** `_request_reasons` in `services/scaffold.py` refuses them too, as defense in depth.
- **Not in scope:** inventory and diagnosis are unchanged, and no existing `fleet-clusters` name contains `-archive` before its end.
- **Docs:** remove "not yet enforced" from the `cluster_name` row in the [Phase 2 contract](05-phase-2-proposals.md#proposal-contract).
- **Tests first:** a contract negative, a CLI argument test for each `propose` subcommand, and a scaffold refusal.

## Stage 2: Phase 0 Closure

| Task | Work | Done when |
| --- | --- | --- |
| 2.1 | Add `tests/test_contract_schemas.py`: every contract model (`CommandEnvelope`, `EvidenceSnapshot`, `EvidenceItem`, `AuditEvent`, `CWASnapshot`, `CWATrace`, `DiagnosisResponse`, `DiagnosisRecord`, `Proposal`, `ScaffoldRun`, `ScaffoldRequest`) exports a JSON Schema whose `properties` match the model's fields (D4) | Test passes |
| 2.2 | Write `docs/plans/11-phase-0-1-acceptance.md`, Phase 0 part: C0.1–C0.7, each with cited tests, in the format of [Phase 2 Acceptance](09-phase-2-acceptance.md) | Every row graded with evidence |
| 2.3 | Add Status and Validation Record sections to [Phase 0](03-phase-0-baseline.md) linking to the matrix | Phase 0 has a closure record |

## Stage 3: Phase 1 Re-proof

| Task | Work | Done when |
| --- | --- | --- |
| 3.1 | Phase 1 part of `11-phase-0-1-acceptance.md`: C1.1–C1.16 with cited tests at the current commit. Where a criterion was proved only by the 2026-10-20 live run, add a test | Every row graded with test evidence |
| 3.2 | With operator approval, re-run the Phase 1 live checks in [Validation](../validation.md): Git, OpenShift, and combined inventory; workflow inspect; diagnosis success and each refusal; idempotency; RBAC `can-i` | Results recorded in Validation |
| 3.3 | Update the [Phase 1](04-phase-1-read-only-assistant.md) Status to point at the new record | Done |

## Stage 4: Phase 2 Caveats

| Task | Work | Done when |
| --- | --- | --- |
| 4.1 | `tests/test_lifecycle_facts.py`: one test per lifecycle fact in the [Phase 2 plan](05-phase-2-proposals.md#lifecycle-facts), named for the row, asserting the trigger effect and commit-message rule the generator reports. Add a column to that table naming each test, or "no Aura behavior" with the reason for the pull request, other-branch, and merge-method rows | Every row has a test or a stated reason |
| 4.2 | `trigger_effect must be` contract negatives for scaffold, cluster-edit, and rerun | Tests pass |
| 4.3 | Generalize `GitDefinitionCheck` in `src/aura/scaffold_runner.py` into `GitPatchCheck.check(*, base_commit, patch, cluster_name, require_definition)`. Pass it to the deprovision (`require_definition=False`), cluster-edit, and scaffold generators through their constructors, and wire it in `cli._proposal_generator`. Existing generator tests may change only to pass a stub check that returns no reasons | Each Git generator refuses a patch that does not apply; cluster-edit and scaffold also refuse an incomplete definition |
| 4.4 | In `_validate_metadata_edit` in `src/aura/proposals.py`, reject a cluster-edit whose resulting `metadata.yaml`, existing lines included, has a key the redaction policy removes: `metadata.yaml must not contain secret-named keys` | Contract negative passes; `htpasswd-provider-name` is not affected |
| 4.5 | With operator approval, re-run live validation for all four proposal types at the closure commit, recording "no new PipelineRun" and protected-repository checks for each | Results recorded in Validation; A1, A12, and A13 caveats closed |
| 4.6 | Update [Phase 2 Acceptance](09-phase-2-acceptance.md) for every closed caveat | Matrix current |

## Stage 5: Closure Gate

| Task | Work |
| --- | --- |
| 5.1 | Give an independent reviewer ungraded copies of the Phase 0, 1, and 2 matrices and the [review brief](#review-brief); grade blind |
| 5.2 | Fix only criterion or invariant violations, under R3–R5; everything else goes to the backlog |
| 5.3 | Operator sign-off for Phases 0, 1, and 2; update the [plans index](readme.md) and [Status](../status.md); the operator publishes |

## Review Brief

Use for every review in this plan:

> Review commits `<range>` in the Aura repository. Report only (a) violations of the closure criteria in `docs/plans/10-phase-0-2-closure.md`, (b) violations of invariants I1–I9 in `docs/development.md`, and (c) regressions: an existing behavior that changed without the plan saying so. For each, give file:line evidence and a reproduction. List anything else under "Backlog notes" in one line each, without severity. Read-only: do not edit tracked files, and work only in the scratchpad.

## Operator Decisions

Accepted 2026-10-25 as recommended.

| ID | Question | Decision |
| --- | --- | --- |
| D1 | The testing plan said an edit value without evidence is refused; the cluster-edit decision uses `fleet` defaults | The cluster-edit decision; testing plan amended |
| D2 | Rollout Gate 2 required staging; no staging hub exists | The read-only production hub qualifies for read-only phases; staging is required before Phase 3 execution |
| D3 | Security requirement 8, rate limiting, was not implemented | A per-command budget of `oc` calls and per-call timeouts now (task 1.3); full rate limiting in Phase 3 |
| D4 | Phase 0 lists "Command JSON Schemas" | The Pydantic contracts are the schemas, with an export test (task 2.1) |
| D5 | Phase 2 open questions 1–4 | Deferred to the backlog with owners |
| D6 | `fleet` removes every `-archive` substring from deprovision sentinel names | Refuse `-archive` anywhere in a proposal cluster name (task 1.4) |

## Progress

| Stage | Task | State | Commit |
| --- | --- | --- | --- |
| 0 | Freeze | Done | `36d7be0` |
| 1 | 1.1 CLI exception boundary | Done | `bbeb78b`, `d6170a7` |
| 1 | 1.2 Process chokepoint | Done | `f1bed5a` |
| 1 | 1.3 `oc` call budget | Done | `5a7cce0` |
| 1 | 1.4 Cluster name rule | Done | `08f40da` |
| 2 | 2.1–2.3 Phase 0 closure | Done | `df58f13`, `dd2e19b`, `8bceb02` |
| 3 | 3.1–3.3 Phase 1 re-proof | Done | `5345ff6`, `2b67fd3`, `1559184` |
| 4 | 4.1–4.6 Phase 2 caveats | In progress: 4.1–4.4 done, 4.6 partial; 4.5 awaits approval | `e601f5f`, `6976c58`, `5f5fe5f`, `b593bf2` |
| 5 | 5.1–5.3 Closure gate | Pending | |

## Backlog

Not blocking Phases 0–2 (R2).

| Item | Target |
| --- | --- |
| Anchor stored proposal digests to the `proposal_created` event or CWA trace | Phase 3 guard |
| Live-state staleness, comparing a defined action subset | Phase 3 guard |
| Command store treats a misnamed record as not found | Phase 3 hardening |
| `git_checked` is always true; keep or remove in a schema revision | Next schema change |
| `pinned_git_revision` first-match versus last-match has no test | Next schema change |
| Output size limits for scaffold streams and files | Phase 3 hardening |
| Scaffold risks do not tell the operator to push the scaffold commit alone | Phase 2 polish after closure |
| Phase 2 open questions 1–4 (D5), including stranded deprovision sentinels | `fleet-clusters` and `fleet` maintainers; edit kinds in Phase 3 |
| Move legacy rerun records out of `local/rerun/proposals/` | Operator |
| A stored proposal whose cluster name contains `-archive` no longer loads after `08f40da`; none exist under `local/` | Operator |
| When `_fail_unexpected` cannot read `--audit-dir`, the command stores `failed` without `command_failed`; an unrelated malformed audit record is enough | Phase 3 hardening |
| `command_failed` from `_fail_unexpected` stores the raw exception text without redaction | Phase 3 hardening |
| An `OSError` a route does not handle reports as `internal error` rather than as a filesystem error | Phase 3 hardening |
| A refusal path that raises after `command_refused` ends the chain `command_refused` then `command_failed` | Phase 3 hardening |
| `tests/test_architecture.py` checks only `subprocess` imports, not `os.system`, `os.posix_spawn`, or `os.exec*` | Phase 3 hardening |
| `ProcessError` reports `cannot run` when the cause is a missing `cwd` | Phase 3 hardening |
| A failed `oc` read retried within one command spends a second budget call | Phase 3 rate limiting |
| `oc` runs with standard input closed, so an interactive exec-credential kubeconfig cannot prompt | Operator: use a token kubeconfig |
| Failure-matrix model-failure rows do not cover the diagnosis model in `inventory-diagnosed` | Next failure-matrix change |
| A non-`OSError` raised by the terminal command record write itself can still leave a stored proposal next to a failed command (review F2, option A) | Phase 3 guard: require a `succeeded` command record before acting on a proposal |
| The Phase 1 live RBAC check covered ClusterDeployments, ManagedClusters, PipelineRuns, and Pipelines; the TaskRuns grant's write verbs were not checked live (the manifest test covers them) | Next live validation |
| The C1.1 checkout tests compare files only, not directory modification times or new empty directories | Next Git collector change |
| `OpenShiftResource.resource_version()` reports `unknown` when an `oc` list lacks `metadata.resourceVersion`; real lists always carry it, and test runners omit it | Phase 3 hardening |
| `tests/test_contract_schemas.py` compares only property names: a changed field type, `extra="allow"`, or a nested model passes it, and the adapter contracts are not exported | Next schema change |
| Phase 0 deliverable 8 has one fixture, from `fleet-clusters`; none from the other protected repositories | Operator |
