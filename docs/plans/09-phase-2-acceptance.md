# Phase 2 Acceptance

## Status

Draft for operator sign-off, prepared 2026-10-25 at Aura commit `c2d64a3` and updated after the fixes in [Resolutions](#resolutions), including closure Stage 4 tasks 4.1–4.5. Each row grades one [Phase 2 acceptance criterion](05-phase-2-proposals.md#acceptance-criteria) against cited evidence. The grades are reconciled: the author graded first, an independent reviewer then graded an ungraded copy of the evidence without seeing those grades, and where they differed the stricter grade was kept once its evidence was reproduced; see [Independent Grading](#independent-grading). **Every row is now met or met with caveat. Phase 2 awaits operator sign-off:** accept the caveats below or ask for them to be closed, then make the Phase 3 changes in [Phase 3 Impact](05-phase-2-proposals.md#phase-3-impact) before Phase 3 starts.

Grades:

| Grade | Meaning |
| --- | --- |
| Met | Every applicable proposal type has evidence that fails when the rule breaks |
| Met with caveat | Met, with a stated limit the operator should accept or close |
| Not met | The behavior the criterion describes is missing |
| Spec contradiction | The criterion conflicts with another approved rule; the spec must change |
| Not applicable | The criterion does not apply to that proposal type |

Evidence notation: `file::test` names a test in `tests/`; a parametrized case is named by its expected message. "Live" cites a dated row in [Validation](../validation.md). Unless noted, every cited test was graded at `c2d64a3`; the current count and `act` result are in [Validation](../validation.md).

## Live Validation

| Proposal type | Command | Live result | Aura commit | Note |
| --- | --- | --- | --- | --- |
| Deprovision | `propose deprovision` | 2026-10-23, success and refusals | `c5abafb` | Predates the empty-sentinel contract (`554b1c6`) and Git isolation (`1c593b2`); not re-run |
| Cluster edit | `propose cluster-edit` | 2026-10-24, add, append, run value, refusal | `dfe32d4` | Predates the 2026-10-25 tier revision and Git isolation; not re-run |
| Post-provision rerun | `propose post-provision-rerun` | 2026-10-24 refusal; 2026-10-25 success | `a439dd7`, `30878cd` | Predates the tier revision and Git isolation; not re-run. The recorded result does not report the expected "no new PipelineRun" or protected-repository checks |
| Scaffold | `propose scaffold` | 2026-10-25, success, name in use, shared infra prefix | `42ad38f` | |
| All four | Each `propose` command | 2026-10-26: deprovision, rerun, and scaffold proposed; cluster-edit refused with every key declared | `1d1bc1c` | Closure re-run with PipelineRun and protected-repository checks before and after each command; see [Phase 2 Closure Re-run](../validation.md#phase-2-closure-re-run) |

## Acceptance Criteria

| # | Criterion | Deprovision | Cluster edit | Rerun | Scaffold | Grade |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | Each proposal type is generated for a representative cluster and is schema-valid | `test_deprovision_proposal::test_generator_proposes_standalone_archive_sentinel`; `test_proposals::test_proposal_accepts_each_type_and_round_trips`; Live | `test_cluster_edit_proposal::test_generator_backfills_missing_metadata_file`; Live | `test_rerun_proposal::test_generator_copies_the_source_run`; Live | `test_scaffold_proposal::test_generator_proposes_the_scaffold_output`; `test_cli_scaffold::test_cli_proposes_a_scaffold`; Live | Met |
| A2 | Every Git proposal applies cleanly to its `base_commit` with `git apply --check` | `test_deprovision_proposal::test_generator_proposes_standalone_archive_sentinel`; Live | `test_cluster_edit_proposal::apply` in the backfill, append, and tier tests; Live | Not applicable: no patch | `test_scaffold_proposal::test_generator_patch_applies_and_yields_a_complete_definition`; `test_scaffold_runner::test_check_refuses_a_patch_that_does_not_apply`; Live. Every Git generator applies its patch during generation: `test_patch_check::test_patch_check_refuses_a_patch_that_does_not_apply`, `test_deprovision_generator_refuses_what_the_patch_check_reports`, `test_cluster_edit_generator_refuses_what_the_patch_check_reports`, `test_cli_wires_the_patch_check_to_the_fleet_clusters_checkout` | Met |
| A3 | Every Git proposal touches exactly one cluster | `test_proposals::test_proposal_rejects_invalid_contract` (`must add only deprovision/<id>-archive`) | `test_proposals::test_proposal_rejects_invalid_contract` (`may change only provision/<id>/metadata.yaml`) | Not applicable as a patch; `test_rerun_proposal::test_generator_refuses_unmet_preconditions` (`is for cluster`); `cluster-name` and `generateName` are pinned by the contract | `test_proposals::test_proposal_rejects_invalid_contract` (`outside provision/<id>/`); `test_scaffold_proposal::test_generator_refuses_unusable_scaffold_output` (`wrote outside`) | Met with caveat |
| A4 | Applying a scaffold or edit proposal to its base commit yields a complete `cluster_definition` | Not applicable | `test_cluster_edit_proposal::test_generator_backfills_missing_metadata_file` and `test_generator_appends_missing_keys` collect the applied tree | Not applicable | Checked during generation by `GitDefinitionCheck`: `test_scaffold_runner::test_check_refuses_an_incomplete_definition`, `test_scaffold_proposal::test_generator_refuses_what_the_definition_check_reports`; Live. Cluster-edit is also checked during generation: `test_patch_check::test_patch_check_without_a_definition_checks_only_that_it_applies`, `test_cluster_edit_generator_refuses_what_the_patch_check_reports` | Met |
| A5 | Every proposal states its trigger effect, and tests cover each row of the lifecycle facts table | Trigger asserted in `test_generator_proposes_standalone_archive_sentinel`; contract negative `trigger_effect must be` | Trigger `[]` asserted in the backfill test | Trigger asserted in `test_rerun_proposal` | `test_scaffold_proposal::test_generator_states_the_provision_chain_and_commit`. Every lifecycle fact has a named test or a stated reason in `test_lifecycle_facts` and the [Lifecycle Facts](05-phase-2-proposals.md#lifecycle-facts) Test column; `test_proposals::test_proposal_rejects_another_trigger_effect` covers every type | Met |
| A6 | An edit proposal that adds a file under an existing `provision/<id>` always requires `[skip-provision]` | Not applicable | `test_proposals::test_proposal_rejects_invalid_contract` (`must contain [skip-provision]`); backfill test asserts it and the head-commit risk | Not applicable | Not applicable; contract forbids it (`must not contain [skip-provision]`) | Met |
| A7 | A deprovision proposal always uses `-archive` and is refused when any sentinel already exists | Contract negatives `must add only deprovision/<id>-archive` and `as an empty regular file`; `test_deprovision_proposal::test_generator_refuses_unmet_preconditions` for both sentinel forms; `test_cli_propose::test_cli_propose_deprovision_refuses_existing_sentinel` | Not applicable | Not applicable | Not applicable | Met |
| A8 | A post-provision rerun proposal is refused when a required parameter is missing or an unknown parameter is present | Not applicable | Not applicable | `test_rerun_proposal::test_generator_refuses_unmet_preconditions` (`lacks required parameters`, `does not declare`) | Not applicable | Met |
| A9 | Every proposal references its evidence snapshot, CWA trace, and base commit, and carries a verified `proposal_digest` | References asserted by `test_cluster_state_diagnosis::assert_references` in `test_generator_proposes_standalone_archive_sentinel`; `test_proposals::test_proposal_rejects_changed_content_under_old_digest`; `test_proposal_store::test_json_proposal_store_rejects_tampered_record` | References asserted by `assert_references` in the backfill test | References and `base_commit` from the evidence's pinned revision asserted in `test_generator_copies_the_source_run`; `test_generator_refuses_without_a_pinned_git_revision`; contract `base_commit is required for post_provision_rerun proposals` | References asserted in `test_generator_proposes_the_scaffold_output`, plus `scaffold_commit` | Met |
| A10 | Proposals contain no secret values | Fixed sentinel content | Values from evidence; redacted run values refuse (`parameter … is redacted`) | Contract negatives `must not contain secret material`; redacted-parameter refusal | `test_scaffold_proposal` secret-field and invalid-YAML refusals and stream-redaction tests; a cluster-edit's whole resulting `metadata.yaml` is checked for secret-named keys: `test_proposals::test_proposal_rejects_cluster_edit_leaving_a_secret_named_key` | Met with caveat |
| A11 | A proposal whose target cluster's `fleet-clusters` paths changed after its `base_commit` is reported as stale (revised 2026-10-25) | `test_git_staleness::test_staleness_reports_changes_to_target_cluster`; `test_cli_proposal_status::test_cli_proposal_status_reports_stale_proposal` | Same Git check | `test_git_staleness::test_staleness_reports_a_rerun_whose_metadata_changed`, `test_staleness_ignores_other_clusters_for_a_rerun`, `test_staleness_fails_closed_without_a_base_commit` | `test_scaffold_proposal::test_scaffold_proposal_goes_stale_when_its_paths_change` | Met with caveat |
| A12 | No Git write, Kubernetes write, or pipeline start occurs | `oc` clients issue only `get`: `test_openshift_cli_client`, `test_tekton_cli_client`, `test_pipeline_definitions`; `test_deploy_manifests` (list-only grants); Live (`fleet-clusters` unchanged) | Same | Same; the manifest is never applied | Same; `test_scaffold_runner::test_runner_changes_neither_checkout`, `test_check_ignores_inherited_git_environment`, `test_runner_and_check_run_no_git_hooks`; Live 2026-10-26: no new PipelineRun and no protected-repository change for every type ([Phase 2 Closure Re-run](../validation.md#phase-2-closure-re-run)) | Met with caveat |
| A13 | No protected repository is modified | Live protected-repository checks | Live | Live | `test_cli_scaffold::test_cli_leaves_both_checkouts_unchanged`; Live (status, refs, objects, index); Live 2026-10-26 for every type: `HEAD`, refs, and status of all four protected repositories unchanged before and after each command | Met |

## Failure-Mode Criteria

| # | Criterion | Evidence | Grade |
| --- | --- | --- | --- |
| F1 | Each contract rule that says only, exactly, or empty is enforced by the contract and has a negative test | `test_proposals::test_proposal_rejects_invalid_contract` and `test_proposal_rejects_invalid_scaffold` (35 scaffold cases, mutation-checked); `scaffold_commit is required for scaffold proposals only` for each other type | Met |
| F2 | A proposal file is written only after its CWA trace and `proposal_created` event | `test_failure_matrix::test_clean_run_writes_in_documented_order` for `propose`, `cluster-edit-*`, `rerun`, `scaffold` (`PROPOSED`: CWA trace, `context_assembled`, `proposal_created`, then the proposal); `test_cli_propose::test_cli_propose_deprovision_stores_proposal_after_provenance` | Met |
| F3 | Every `propose` and `proposal` command failing after the idempotency check stores a `failed` record, and `command_failed` links to the last stored event | `test_failure_matrix::test_each_write_failure_fails_closed`, `test_evidence_read_failure_fails_closed`; `test_cli_scaffold::test_cli_fails_when_the_definition_check_fails` | Met with caveat |
| F4 | `proposal status` fails when the store is missing or unreadable, or a record is misnamed, unreadable, or tampered | `test_cli_proposal_status::test_cli_proposal_status_fails_for_*`, including `fails_for_a_blank_digest`; `test_git_staleness::test_staleness_collector_rejects_*`, including blank-digest and renamed records; `test_proposal_store::test_json_proposal_store_requires_the_stored_digest`, `rejects_a_record_under_another_name` | Met |
| F5 | Each proposal type has failure tests for every row of its matrix | `test_failure_matrix` scenarios `propose`, `propose-refused`, `cluster-edit-file`, `cluster-edit-keys`, `cluster-edit-refused`, `rerun`, `rerun-refused`, `scaffold`, `scaffold-refused`, `proposal-status*`: 103 tests in the file | Met with caveat |

## Findings

Blocking at grading time, all closed since; see [Resolutions](#resolutions):

1. **A9.** Reruns carry no `base_commit`, because a rerun changes no Git file, so the criterion as written cannot hold for them; narrow it to Git proposals, or pin reruns to the `fleet-clusters` revision their evidence read. Separately, only the deprovision tests assert a proposal's evidence snapshot and CWA references: setting `evidence_snapshot_id` to the CWA snapshot ID in the cluster-edit, rerun, or scaffold generator passes their tests. "Verified digest" is also weakened by F4.
2. **A11.** The spec counts a proposal stale when its evidence no longer matches live state; only the Git half exists, and `test_git_staleness::test_staleness_skips_git_check_for_reruns` pins reruns as never stale. Changes to `cluster-templates/` or a newly colliding infra prefix after the base commit do not mark a scaffold stale either. Implement live-state staleness, at least for reruns, or narrow the criterion and make live staleness a Phase 3 guard.
3. **F1.** The contract does reject `scaffold_commit` on other proposal types, but no negative test pins it; weakening the check passes the suite. Two type rules are enforced by generators by design, because the contract cannot see their inputs: cluster-edit's `ai`-tier `openshift-ai-version` and the rerun's undeclared parameters. The spec should say F1 covers rules the contract can see.
4. **F4.** A tampered record with a blank or missing `proposal_digest` loads as valid, because an empty digest is recomputed on validation, and `proposal status` reports it. A record saved under another valid UUID file name loads under that name with a different internal ID. Both were reproduced. Require a non-empty stored digest, compare the file name with `proposal_id`, and, before Phase 3 relies on "digest changed", anchor the digest to the `proposal_created` audit event or the CWA trace's `response_digest`.

Caveats:

5. **A1, A12, A13.** Closed by closure task 4.5 (`faea287`): all four types were re-run live on 2026-10-26 at `1d1bc1c`, with no new PipelineRun and no protected-repository change before and after each command.
6. **A2, A4.** Closed by closure task 4.3 (`5f5fe5f`): every Git generator applies its patch during generation, and cluster-edit and scaffold also require one complete definition.
7. **A3.** The cluster-edit "may change only" negative uses another file of the same cluster; none uses another cluster's path.
8. **A5.** Closed by closure tasks 4.1 (`e601f5f`) and 4.2 (`6976c58`): each lifecycle row names its test or states why Aura has no behavior to test, and every proposal type has a `trigger_effect must be` negative.
9. **A10.** Secret checks are field-name based, so a secret value under a non-secret name is not detected. The cluster-edit part is closed by closure task 4.4 (`b593bf2`): the contract rejects a resulting `metadata.yaml` with any secret-named key, existing lines included.
10. **A12.** `propose scaffold` executes code from the `fleet-cluster-scaffold` protected repository, isolated but not sandboxed. RBAC protects the hub only when the read-only kubeconfig is used.
11. **F3.** If the terminal command-record write itself fails, a stored proposal can remain with no command record; the failure-mode matrix allows this. Closure Stage 1 (`f617dbe`) moved every other step before the proposal write; the Phase 3 guard must require a `succeeded` command record before acting on a proposal.
12. **F5.** Read-error injection replaces the whole collector, so it tests error routing, not which source failed.

Outside the criteria, found during grading. Their disposition is in the [closure plan](10-phase-0-2-closure.md): 13 is Stage 1 task 1.4, 15 was fixed in Stage 0, 16 is decision D5 and the Stage 5 review, and 14 is in the backlog.

13. **Cluster names containing `-archive`.** `fleet` derives the cluster name from a deprovision sentinel with `.replace('-archive', '')` (`fleet` `tekton/triggers/eventlistener.yaml`), which removes every occurrence, while Aura removes only the suffix and `CLUSTER_NAME_PATTERN` allows the substring. A deprovision proposal for a name such as `a-archive-b` would start deprovision of `a-b`, and scaffold could create such a name. No current `fleet-clusters` name contains `-archive` before its end. Refuse the substring in proposal cluster names.
14. **Scaffold commit guidance.** Scaffold risks do not say to push the commit alone, though only the first cluster name in a push triggers a pipeline.
15. **Documents.** `docs/evidence.md` still calls the scaffold generator "not yet implemented"; `docs/development.md` says 101 matrix tests (103 now); and the plan's note says only deprovision and cluster-edit meet the failure-mode criteria.
16. **Open questions and review.** The four open questions in the Phase 2 plan remain open, and the review fixes in `0ba7124` have not had a second independent review.

## Resolutions

| Row | Change | Commit | Evidence | Remaining |
| --- | --- | --- | --- | --- |
| F4 | The proposal store requires a non-empty stored `proposal_digest`; every JSON record store rejects a record whose ID differs from its file name | `341f9a3` | Store, staleness, and CLI tests for blank, null, and missing digests and a renamed record; disabling either check fails them | Anchoring the digest to the `proposal_created` event or CWA trace remains a Phase 3 guard |
| F1 | Contract negatives for `scaffold_commit` on deprovision, cluster-edit, and rerun proposals | `9ca51b3` | `test_proposals::test_proposal_rejects_invalid_contract` | Generator-only type rules are by design; the spec should say F1 covers rules the contract can see |
| A9 and A11, decision | Pin reruns to the `fleet-clusters` revision their evidence read; narrow A11 to Git staleness and move live staleness to the Phase 3 guard | `f01b3bf` | Phase 2 plan: Proposal Contract, staleness, Acceptance Criteria, Phase 3 Impact | None |
| A9 and A11, implementation | The contract requires `base_commit` on reruns; the rerun generator sets it from the evidence's pinned Git revision and refuses without one; Git staleness checks reruns, and a proposal without a base commit is reported stale | `8193210` | Contract, generator, and staleness tests; four rules mutation-checked | Live-state staleness is the Phase 3 guard; rerun proposals stored without a `base_commit` must be moved aside and generated again |
| Review of the fixes | A deeply nested proposal file raised an uncaught `RecursionError`; it now fails as an unreadable record. Stale docs corrected; the Phase 3 guard compares a defined action subset, not the full digest | `812a8c7` | `test_staleness_collector_rejects_unreadable_records[_deeply_nested]` | The command store treats a misnamed record as not found, which predates this work |
| A2, A4 | `GitPatchCheck` applies every Git proposal's patch during generation; cluster-edit and scaffold also require one complete definition | `5f5fe5f` | `test_patch_check` | None |
| A5 | One named test per lifecycle fact, and `trigger_effect must be` negatives for every type | `e601f5f`, `6976c58` | `test_lifecycle_facts`; `test_proposal_rejects_another_trigger_effect` | None |
| A1, A12, A13 | Live re-run of all four types at the closure tasks' commit, with no-new-PipelineRun and protected-repository checks | `faea287` | [Phase 2 Closure Re-run](../validation.md#phase-2-closure-re-run) | A12: the scaffold runs protected-repository code isolated but not sandboxed |
| A10 | The contract rejects a cluster-edit whose resulting `metadata.yaml` has a secret-named key | `b593bf2` | `test_proposal_rejects_cluster_edit_leaving_a_secret_named_key` | Value-based secret detection |
| A9, references | Every generator's success test asserts its evidence snapshot ID and digest, CWA snapshot ID, and trace ID | `9ca51b3` | Swapping any of the three references in any of the four generators fails a test (12 mutations) | The rerun base commit decision |

After the F1, F4, and A9 reference changes, 835 tests passed at `ca37a83`; after rerun pinning, 840 passed at `8193210`; after the review fixes, 841 passed at `812a8c7`; each locally and under `act`, with zero gating findings on each committed tree.

## Independent Grading

The author graded first. An independent reviewer then received an ungraded copy, with the grade columns and findings removed, verified each citation, ran mutation probes in a `git archive` copy, and graded blind. The stricter grade is used above wherever the reviewer's evidence was reproduced.

| # | Author | Reviewer | Used | Reason for the difference |
| --- | --- | --- | --- | --- |
| A1 | Met with caveat | Met with caveat | Met with caveat | Reviewer also flagged the deprovision live run as stale |
| A2 | Met | Met with caveat | Met with caveat | Patch application is checked during generation only for scaffold |
| A3 | Met | Met with caveat | Met with caveat | Cluster-edit negative uses a same-cluster path |
| A4 | Met with caveat | Met with caveat | Met with caveat | |
| A5 | Met with caveat | Met with caveat | Met with caveat | |
| A6 | Met | Met | Met | Reviewer confirmed by mutation |
| A7 | Met | Met | Met | Reviewer confirmed by mutation |
| A8 | Met | Met | Met | Reviewer confirmed by mutation |
| A9 | Spec contradiction | Not met | Not met | Reference assertions are missing for three types, beyond the rerun base-commit conflict |
| A10 | Met | Met with caveat | Met with caveat | Verbatim `metadata.yaml` context is not scanned |
| A11 | Not met | Not met | Not met | |
| A12 | Met with caveat | Met with caveat | Met with caveat | Reviewer added the `oc get` client evidence |
| A13 | Met | Met with caveat | Met with caveat | Rerun live run records no repository check |
| F1 | Met with caveat | Not met | Not met | No negative for `scaffold_commit` on other types; reproduced |
| F2 | Met | Met | Met | |
| F3 | Met | Met with caveat | Met with caveat | Terminal-record write failure can leave a proposal without a record |
| F4 | Met | Not met | Not met | Blank-digest and misnamed-record bypasses; both reproduced |
| F5 | Met | Met with caveat | Met with caveat | Collector-level read-error injection; count corrected to 103 |
