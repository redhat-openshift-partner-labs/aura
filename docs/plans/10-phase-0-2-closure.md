# Phases 0–2 Closure Plan

## Status

Approved 2026-10-25 at Aura commit `3b73a3f`; the operator accepted D1–D6 as recommended. Stage 0 is complete: the decisions are applied to the governing plans, deferred items live only in the [Backlog](#backlog), and current verification results live only in [Validation](../validation.md). Stage 1 is next.

## Diagnosis

Every fix round since the scaffold work found new issues. They were of three kinds:

| Kind | Examples | Share |
| --- | --- | --- |
| Latent defects a wider review newly looked at | Inherited `GIT_DIR` read another repository; ManagedCluster-only names; `fleet` strips every `-archive` substring | Most |
| Defects a fix introduced | `json.loads` let `RecursionError` escape; a test inserted under another test's decorator; a false "zero gating findings" record | Several |
| Docs left stale by a change | Rerun `base_commit` "absent"; "not yet implemented"; test counts and `act` commits | Every round |

Root causes, by asking why each kind kept recurring:

1. **No frozen definition of done.** Phases 0 and 1 have no current closure record, Phase 2's was built after the code, and every review was open-ended ("find problems"). Each review therefore widened the scope, and every finding was treated as blocking.
2. **Classes fixed one instance at a time.** Escaping exceptions (`RecursionError`, `UnicodeDecodeError`, `TarError`), subprocess isolation, and record integrity were each fixed where they were found. The CLI has no exception boundary after the idempotency check: errors are caught by ten enumerated tuples per route, so any unlisted exception prints a traceback and stores no `failed` record (I1, I7). Seven `subprocess.run` sites use three different isolation styles; the `oc` client still inherits standard input and decodes text, so it can raise the same `UnicodeDecodeError` fixed in the scaffold runner.
3. **No structural enforcement of cross-cutting invariants.** Nothing fails the build when a new code path calls `subprocess` directly, raises an unlisted exception, or restates a doc fact. Correctness depends on each change remembering every rule.
4. **Volatile facts duplicated across documents.** Test counts, the latest `act` commit, and contract facts are restated in up to seven documents, so every change leaves some stale.
5. **Verification run against the wrong tree.** The quality gate analyzes committed `HEAD`; runs before committing checked the previous commit.

## Closure Rules

These rules bound the remaining work so it ends:

| Rule | Statement |
| --- | --- |
| R1 Frozen scope | Closure means the criteria in [Closure Criteria](#closure-criteria) and nothing else. The list changes only by an operator decision recorded here. |
| R2 Triage | A new finding blocks closure only if it violates a closure criterion or a project invariant (I1–I9). Anything else goes to [Backlog](#backlog) with a target phase, and is not fixed during closure. |
| R3 Classes, not instances | A defect that belongs to a class is fixed at a single chokepoint, with a structural test that fails when any code path bypasses it. |
| R4 Verification order | Tests, `git diff --check`, commit, then the quality gate with `target_ref` equal to the new commit, then `act`. Volatile results are recorded only in the [Latest Verification](../validation.md) record. |
| R5 One source per fact | Each fact lives in one document; others link to it. |
| R6 Bounded reviews | Reviewers get the frozen criteria and the diff, and report only criterion or invariant violations; everything else is a backlog note. |
| R7 Stop rule | A phase is closed when every criterion is met or met with an operator-accepted caveat, and one bounded review reports no violation. |

## Operator Decisions

Accepted 2026-10-25 as recommended.

| ID | Question | Decision |
| --- | --- | --- |
| D1 | [Testing plan](07-testing-security-rollout.md#proposal-generator-tests) says an edit value without evidence is refused; the 2026-10-23 cluster-edit decision uses `fleet` defaults. Which governs? | The cluster-edit decision; amend the testing plan |
| D2 | Rollout Gate 2 requires staging, but no staging hub exists. | Accept the production hub with the read-only service account for read-only phases; require staging before any Phase 3 execution |
| D3 | Security requirement 8, "all external calls are rate limited", is not implemented. | Implement a per-command call budget and timeout at the process chokepoint (Stage 1); full rate limiting is a Phase 3 concern |
| D4 | Phase 0 lists "Command JSON Schemas". Aura's schemas are Pydantic models. | Pydantic models are the schemas; add a test that every contract model exports a JSON Schema |
| D5 | Phase 2 open questions 1–4 (stranded remediation, `fleet-clusters` README sentinel form, unauthenticated post-provision trigger, further edit kinds) | Defer all four to the backlog with owners; none blocks Phases 0–2 |
| D6 | `fleet` removes every `-archive` substring from deprovision sentinel names. | Refuse `-archive` anywhere in a proposal cluster name; it is a correctness hazard for A3 and A7 |

## Closure Criteria

Phase 0, from [Phase 0](03-phase-0-baseline.md):

- C0.1–C0.6: the six acceptance criteria.
- C0.7: deliverables 1–8 exist or are explicitly superseded (D4).

Phase 1, from [Phase 1](04-phase-1-read-only-assistant.md):

- C1.1–C1.16: the sixteen acceptance criteria, re-proved at the closure commit.
- C1.17: Phase 1 live validation re-run at the closure commit.

Phase 2, from [Phase 2](05-phase-2-proposals.md) and [Phase 2 Acceptance](09-phase-2-acceptance.md):

- C2.1–C2.18: A1–A13 and F1–F5, with every caveat closed or operator-accepted.
- C2.19: the testing plan's generator test table, including one trigger-effect test per lifecycle fact.
- C2.20: live validation of all four proposal types at the closure commit.

Cross-cutting:

- CX.1: invariants I1–I9 hold for every command, proved by the shared harness.
- CX.2: security requirements 1–10 of the [testing plan](07-testing-security-rollout.md#security-requirements) that apply to Phases 0–2, per D3.
- CX.3: no protected repository modified.

## Work Plan

Each task follows the [development workflow](../development.md): test plan, failing tests, smallest fix, verification per R4, docs, conventional commit. Stages run in order; tasks within a stage are independent unless noted.

### Stage 0: Freeze (docs only)

| Task | Work | Done when |
| --- | --- | --- |
| 0.1 | Record D1–D6 answers here and apply them to the governing plans: amend the testing plan (D1), Rollout Gate 2 (D2), security requirement 8 (D3), Phase 0 deliverables (D4), Phase 2 open questions (D5), and the cluster name rule (D6) | Plans agree with each other; this document lists the decisions |
| 0.2 | Create the [Backlog](#backlog) from every known non-blocking item | Backlog is the only place they live |
| 0.3 | Remove volatile duplicates: keep current test counts and the latest `act` run only in [Validation](../validation.md); replace restatements in status, plans, and acceptance records with links | No current test count or `act` result appears outside Validation; counts recorded against a named historical commit stay as records |

### Stage 1: Fix classes at chokepoints

| Task | Work | Structural test | Closes |
| --- | --- | --- | --- |
| 1.1 | CLI exception boundary: after the idempotency check, any exception the routes do not handle becomes a `failed` terminal record, `command_failed` linked to the last stored event, `aura: internal error: <type>: <message>` on standard error, and exit `2`, with no traceback | Failure matrix gains an "unexpected error" row for every scenario, raising `RuntimeError`, `RecursionError`, and `UnicodeDecodeError` from the collector, the generator, and a store read | Escaping-exception class; I1, I7 |
| 1.2 | Process chokepoint `aura.process`: one function runs every external program with bytes capture and replacement decoding, `stdin` closed, a timeout, an explicit environment policy (Git isolation, scaffold minimal environment, `oc` inherited environment minus `GIT_`), and typed errors. Migrate all seven call sites | An AST test fails if any module other than `aura.process` imports `subprocess` | Subprocess isolation class; `oc` decode and stdin gaps |
| 1.3 | Per-command call budget at the chokepoint (D3): a command that exceeds its budget of external calls fails closed | Budget test per command | Security requirement 8 for Phases 0–2 |
| 1.4 | Cluster name rule (D6): the shared cluster-name validation and the `Proposal` contract refuse `-archive` anywhere in a name; inventory flags existing names that contain it | Contract negative and CLI argument test | A3 and A7 hazard |

### Stage 2: Phase 0 closure

| Task | Work |
| --- | --- |
| 2.1 | Build a Phase 0 acceptance matrix like [Phase 2 Acceptance](09-phase-2-acceptance.md): each criterion C0.1–C0.7 with cited tests |
| 2.2 | Add the JSON Schema export test (D4) and any fixture test the matrix shows missing |
| 2.3 | Add Status and Validation Record sections to [Phase 0](03-phase-0-baseline.md) |

### Stage 3: Phase 1 re-proof

| Task | Work |
| --- | --- |
| 3.1 | Build a Phase 1 acceptance matrix for C1.1–C1.16 at the current commit, with cited tests; add tests for any criterion proved only by the 2026-10-20 live run |
| 3.2 | Re-run Phase 1 live validation read-only at the closure commit: Git, OpenShift, and combined inventory; workflow inspect; diagnosis success and each refusal; idempotency; RBAC `can-i` |
| 3.3 | Update the Phase 1 validation record |

### Stage 4: Phase 2 caveats

| Task | Work | Closes |
| --- | --- | --- |
| 4.1 | One test per lifecycle fact asserting the reported trigger effect; rows with no Aura behavior (pull requests, other branches, merge methods) recorded as not applicable with the reason | A5, C2.19 |
| 4.2 | `trigger_effect must be` contract negatives for every proposal type | A5, F1 |
| 4.3 | Generalize `GitDefinitionCheck` into a patch check used by every Git proposal at generation: apply to a clone of the base commit; for scaffold and cluster-edit also require one complete definition | A2, A4 |
| 4.4 | Contract scan of the whole resulting `metadata.yaml` in a cluster-edit patch, existing context included, for secret-named keys | A10 |
| 4.5 | Re-run live validation for deprovision, cluster-edit, rerun, and scaffold at the closure commit, recording "no new PipelineRun" and protected-repository checks for every type | A1, A12, A13, C2.20 |

### Stage 5: Closure gate

| Task | Work |
| --- | --- |
| 5.1 | Bounded independent review (R6) of Phases 0–2 against the frozen criteria, grading ungraded matrices blind |
| 5.2 | Fix only criterion or invariant violations, under R3 and R4; everything else goes to the backlog |
| 5.3 | Operator sign-off for Phases 0, 1, and 2; update the plans index and status; the operator publishes |

## Backlog

Not blocking Phases 0–2, per R2. Each item names its target.

| Item | Target |
| --- | --- |
| Anchor stored proposal digests to the `proposal_created` event or CWA trace | Phase 3 guard |
| Live-state staleness, comparing a defined action subset | Phase 3 guard |
| Command store treats a misnamed record as not found | Phase 3 hardening |
| `git_checked` is now always true; keep or remove in a schema revision | Next schema change |
| `pinned_git_revision` first-match versus last-match has no test | Stage 1.2 if touched, otherwise next schema change |
| Output size limits for scaffold streams and files | Phase 3 hardening |
| Phase 2 open questions 1–4 (D5) | Owners: `fleet-clusters` and `fleet` maintainers; edit kinds in Phase 3 |
| Operator: move legacy rerun records out of `local/rerun/proposals/` | Operator |
| Scaffold risks do not tell the operator to push the scaffold commit alone, though only the first cluster name in a push triggers a pipeline | Phase 2 polish after closure |
| Stranded deprovision sentinels (open question 1) | `fleet-clusters` maintainers |

## Sequencing and Effort

Stages 0 and 1 come first because they stop new issues: Stage 1 removes the classes that produced most findings, and Stage 0 stops scope growth. Stages 2–4 are independent of each other after Stage 1. Stage 5 runs once. Rough size: Stage 0 small; Stage 1 the largest, about two to three development cycles; Stages 2 and 3 small to medium; Stage 4 medium; Stage 5 small unless it finds criterion violations.
