# Phases 0 and 1 Acceptance

## Status

Draft for operator sign-off. The Phase 0 part was graded by the author on 2026-10-25 at Aura commit `df58f13` and corrected after the Stage 2 review (see [Review Corrections](#review-corrections)); the Phase 1 part was graded for task 3.1 of the [closure plan](10-phase-0-2-closure.md), and its live criterion C1.17 after the task 3.2 re-run. Stage 5 of that plan has an independent reviewer grade an ungraded copy blind, and the stricter grade is kept once its evidence is reproduced.

Grades:

| Grade | Meaning |
| --- | --- |
| Met | Cited evidence fails when the rule breaks |
| Met with caveat | Met, with a stated limit the operator should accept or close |
| Not met | The behavior the criterion describes is missing |
| Superseded | Replaced by an operator decision recorded in the closure plan |

Evidence notation: `file::test` names a test in `tests/`. Every cited test passes at the grading commit; the current count is in [Validation](../validation.md).

## Phase 0

Criteria C0.1–C0.6 are the [Phase 0 acceptance criteria](03-phase-0-baseline.md#acceptance-criteria); C0.7 is its [deliverables](03-phase-0-baseline.md#deliverables).

| # | Criterion | Evidence | Grade |
| --- | --- | --- | --- |
| C0.1 | All command and evidence schemas validate representative fixtures | `test_commands::test_valid_command_envelope`, `test_command_envelope_round_trips_through_json`, `test_command_envelope_rejects_invalid_fields`; `test_evidence::test_valid_evidence_snapshot`, `test_evidence_snapshot_round_trips_through_json`, `test_evidence_snapshot_rejects_invalid_fields`; `test_audit::test_valid_audit_event`, `test_audit_event_rejects_unknown_fields`; `test_cwa::test_cwa_trace_round_trips_through_json`; `test_diagnosis::test_diagnosis_response_round_trips_through_json`; `test_proposals::test_proposal_accepts_each_type_and_round_trips`; `test_fixtures::test_fleet_clusters_inventory_fixture_is_valid` validates the `fleet-clusters` fixture; `test_contract_schemas::test_contract_exports_a_json_schema_of_its_fields` | Met |
| C0.2 | A read-only Git inventory collector returns typed cluster data | `test_git_inventory_collector::test_git_inventory_collector_returns_typed_evidence`, `test_git_inventory_collector_lists_active_and_archived_clusters`, `test_git_inventory_collector_reads_head_not_working_tree`, `test_git_inventory_collector_ignores_an_inherited_git_dir`, which fails if Git does not run with `isolated_git_environment()`; programs run only through `aura.process` (`test_architecture::test_only_the_process_module_imports_subprocess`) | Met |
| C0.3 | A read-only OpenShift collector returns typed cluster and PipelineRun status | `test_openshift_inventory_collector::test_openshift_inventory_collector_returns_cluster_actual_state`, `test_openshift_inventory_collector_reports_missing_managed_cluster`; `test_tekton_inventory_collector::test_tekton_collector_returns_pipeline_run_with_failed_task`; `test_openshift_cli_client::test_openshift_cli_client_reads_inventory`; `test_tekton_cli_client::test_openshift_cli_tekton_client_reads_workflow_status`; the clients issue only `oc get`, and `test_deploy_manifests::test_every_rule_grants_only_list` keeps the service account list-only | Met with caveat |
| C0.4 | Redaction removes known secret fields before evidence storage | Every command's snapshot passes through `redact_snapshot` in the shared evidence service (`src/aura/services/evidence.py`) before the CLI stores it: `test_evidence_redaction::test_evidence_command_service_redacts_collected_evidence` for each of the six command services, and `test_every_evidence_command_service_is_covered`; `test_inventory_service::test_inventory_service_redacts_collected_evidence`; rules in `test_redaction::test_redacts_default_secret_fields_recursively`, `test_redacts_secret_field_spelling_variants`, `test_redacts_parameters_with_secret_terms`; collector-side redactions kept: `test_tekton_inventory_collector::test_tekton_collector_records_redacted_params_and_workspaces`, `test_combined_collectors_keep_included_tekton_redactions`; `test_fixtures::test_fleet_clusters_inventory_fixture_contains_no_secrets` | Met with caveat |
| C0.5 | Authorization rules reject unauthorized command types | Every role and command type: `test_authorization::test_each_role_may_issue_only_its_command_types`, `test_role_table_covers_every_command_type`, `test_only_approver_or_admin_may_approve`; `test_viewer_cannot_issue_mutating_commands`, `test_proposer_cannot_execute`, `test_viewer_cannot_approve`, `test_empty_roles_are_denied`; service refusals `test_inventory_service::test_inventory_service_rejects_unauthorized_operator`, `test_workflow_service::test_workflow_service_rejects_unauthorized_operator`, `test_cluster_state_service::test_cluster_state_service_rejects_unauthorized_operator`; CLI refusals `test_cli::test_cli_inventory_list_uses_only_supplied_roles`, `test_cli_propose::test_cli_propose_deprovision_requires_proposer_or_admin`, `test_cli_cluster_edit::test_cli_propose_cluster_edit_requires_proposer_or_admin`, `test_cli_rerun::test_cli_rerun_requires_proposer_or_admin`, `test_cli_scaffold::test_cli_scaffold_requires_proposer_or_admin`, `test_cli_proposal_status::test_cli_proposal_status_authorizes_viewer_proposer_and_admin`. The CLI treats an absent `--role` as `viewer`, while the policy denies an empty role set | Met |
| C0.6 | No protected repository is modified | `test_cli_scaffold::test_cli_leaves_both_checkouts_unchanged`; `test_scaffold_runner::test_runner_changes_neither_checkout`, `test_runner_and_check_run_no_git_hooks`, `test_check_ignores_inherited_git_environment`; on 2026-10-25, `git status --porcelain` reported no changes in `fleet` (`9464f0b`), `fleet-cluster-scaffold` (`2f6c7b4`), `fleet-clusters` (`4ead929`), or `portal` (`e75ad41`) | Met with caveat |
| C0.7 | Phase 0 deliverables exist or are superseded | See [Deliverables](#phase-0-deliverables) | Met |

### Phase 0 Deliverables

| # | Deliverable | Where | Evidence |
| --- | --- | --- | --- |
| 1 | Command JSON Schemas | Superseded by D4: the Pydantic contracts are the schemas | `test_contract_schemas::test_contract_exports_a_json_schema_of_its_fields` |
| 2 | Evidence collector output schemas | `EvidenceSnapshot` and `EvidenceItem` in `src/aura/contracts.py`; item kinds in [Evidence](../evidence.md) | `test_evidence` |
| 3 | Adapter interface definitions | `AdapterRequest`, `AdapterResult`, and `ExecutionAdapter` in `src/aura/adapters.py` | `test_adapters` |
| 4 | Authorization model | `src/aura/authorization.py` | `test_authorization` |
| 5 | Secret-redaction rules | `src/aura/redaction.py` | `test_redaction` |
| 6 | Audit event schema | `AuditEvent` in `src/aura/contracts.py`; `JsonAuditEventStore` | `test_audit`, `test_audit_event_store` |
| 7 | Local development environment | `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, [Development](../development.md) | Full suite and `act` results in [Validation](../validation.md) |
| 8 | Initial test fixtures from the protected repositories | `tests/fixtures/fleet-clusters-inventory.json` | `test_fixtures::test_fleet_clusters_inventory_fixture_is_valid`, `test_fleet_clusters_inventory_fixture_contains_no_secrets` |

### Phase 0 Caveats

1. **C0.3.** The collectors are proved with fake `oc` runners. Live proof against the hub is Phase 1 criterion C1.17.
2. **C0.4.** Each command service is proved to redact, and the CLI stores the snapshot that service returns, but no single test stores a snapshot built from secret-bearing evidence and reads the stored file back.
3. **C0.6.** The status check shows the protected checkouts unchanged at grading time; it does not prove that no earlier run wrote to them. Aura's only Git writes go to temporary clones, as the cited tests show.

### Review Corrections

The Stage 2 review mutated the source and found three rows whose cited evidence did not fail when the rule broke:

| Row | Finding | Correction |
| --- | --- | --- |
| C0.3 | `test_manifests_create_only_read_only_access_objects` checks object kinds, not verbs; adding `create` to the ClusterRole passed it | Cite `test_every_rule_grants_only_list`, which fails; regrade met with caveat 1 |
| C0.4 | Redacting only `inventory list` snapshots passed the whole suite | Add `test_evidence_redaction`, which fails for each other command service |
| C0.5 | Letting `executor` or `approver` issue `propose.pipeline`, or `approver` issue `execute.patch`, passed the cited tests | Add the role-by-command-type table test, which fails for each |

## Phase 1

Criteria C1.1–C1.16 are the [Phase 1 acceptance criteria](04-phase-1-read-only-assistant.md#acceptance-criteria), in order; C1.17 is the live re-run. Graded by the author on 2026-10-25 at Aura commit `5345ff6` for closure task 3.1, and corrected after the Stage 3 review, which found that the C1.1, C1.11, and C1.14 evidence did not reliably fail when the rule broke. Where a criterion was proved only by the 2026-10-20 live run, a test was added: C1.1.

| # | Criterion | Evidence | Grade |
| --- | --- | --- | --- |
| C1.1 | Aura lists cluster definitions without writing to Git | `test_git_inventory_collector::test_git_inventory_collector_leaves_the_checkout_unchanged` compares every file and modification time in the checkout, `.git` included, before and after collection, with a dirty working tree and an untracked definition; `test_git_inventory_collector_leaves_a_stat_dirty_index_unchanged` makes a tracked file stat-dirty so that any index refresh rewrites `.git/index`, and fails if collection runs `git status` without isolation | Met |
| C1.2 | Aura reads Git cluster definitions from committed `HEAD`, not the working tree | `test_git_inventory_collector::test_git_inventory_collector_reads_head_not_working_tree`, `test_git_tree_pins_head_for_tree_and_blob_reads`, `test_git_inventory_collector_ignores_untracked_cluster_directories` | Met |
| C1.3 | Aura lists OpenShift resources without write permissions | `test_deploy_manifests::test_every_rule_grants_only_list`; the clients run only `oc get`, asserted by argument in `test_openshift_cli_client::test_openshift_cli_client_reads_inventory` and `test_tekton_cli_client::test_openshift_cli_tekton_client_reads_workflow_status`; live RBAC is C1.17 | Met |
| C1.4 | Aura correlates Git, OpenShift, and Tekton data by cluster name | `test_cluster_inventory_collector::test_cluster_inventory_collector_correlates_sources`, `test_cli_inventory_list_combined_returns_view`; `test_cluster_state_collector::test_cluster_state_collector_correlates_evidence`; `test_workflow_inventory_collector::test_workflow_inventory_collector_correlates_cluster` | Met |
| C1.5 | Aura stores the frozen evidence snapshot | `test_cli::test_cli_inventory_list_persists_snapshot`; `test_cli_workflow::test_cli_workflow_inspect_persists_snapshot_and_audit`; `test_evidence::test_evidence_snapshot_detects_digest_tampering`; `test_evidence_snapshot_store::test_json_evidence_snapshot_store_saves_and_loads` | Met |
| C1.6 | Aura stores the CWA trace and snapshot digest | `test_cli::test_cli_inventory_list_assembles_and_persists_cwa`; `test_cwa_assembler::test_cwa_assembler_builds_snapshot_and_trace`, `test_cwa_assembler_rejects_audit_snapshot_mismatch`; `test_cwa_store::test_json_cwa_trace_store_saves_and_loads` | Met |
| C1.7 | Aura refuses to answer when required evidence is missing | `test_cli_diagnose_state::test_cli_diagnose_state_refuses_missing_evidence`; `test_cluster_state_diagnosis::test_cluster_state_diagnosis_model_reports_missing_evidence`, `test_cluster_state_diagnosis_model_refuses_incomplete_definition`, `test_cluster_state_diagnosis_model_refuses_definition_conflict`; `test_diagnosis::test_diagnosis_service_does_not_fallback_on_missing_evidence` | Met |
| C1.8 | Aura returns schema-valid JSON from every model call | Each answer is a validated `DiagnosisRecord` whose response is a `DiagnosisResponse`: `test_diagnosis::test_diagnosis_response_rejects_invalid_confidence`, `test_diagnosis_response_rejects_blank_summary`, `test_diagnosis_response_rejects_fact_without_evidence_id`; `test_cli_diagnose_state::test_cli_diagnose_state_returns_cluster_diagnosis` parses the output; `test_cli::test_cli_inventory_list_persists_diagnosis_and_model_audit`; `test_diagnosis_store::test_json_diagnosis_store_saves_and_loads` | Met with caveat |
| C1.9 | Aura provides a deterministic fallback answer when the model is unavailable | `test_diagnosis::test_diagnosis_service_uses_fallback_when_model_is_unavailable`, `test_fallback_diagnosis_model_returns_deterministic_response` | Met |
| C1.10 | Aura suppresses completed idempotency keys before evidence collection | `test_cli::test_cli_inventory_list_rejects_duplicate_idempotency_key` asserts one collection, `test_cli_inventory_list_suppresses_concurrent_duplicate_key`; `test_cli_diagnose_state::test_cli_diagnose_state_suppresses_duplicate_idempotency_key` | Met |
| C1.11 | Aura reports OpenShift CLI failures concisely without partial JSON | `test_openshift_cli_client::test_openshift_cli_client_converts_oc_failures`, `test_openshift_cli_client_rejects_oc_output_that_is_not_utf8`; `test_openshift_cli_client_reports_only_the_last_error_line`; `test_cli_workflow::test_cli_workflow_inspect_reports_oc_failure`; `test_process_messages::test_missing_oc_reports_the_os_error` | Met |
| C1.12 | Aura reports incomplete and invalid Git definitions per definition without aborting collection | `test_git_inventory_collector::test_git_inventory_collector_reports_incomplete_cluster`, `test_git_inventory_collector_reports_invalid_definition`, `test_git_inventory_collector_reads_incomplete_definition_sources` | Met |
| C1.13 | Aura correlates incomplete and invalid definitions by cluster name instead of reporting them as missing | `test_cluster_inventory_collector::test_cluster_inventory_collector_reports_incomplete_definitions`, `test_cluster_inventory_collector_reports_invalid_definition`; `test_cluster_state_collector::test_cluster_state_collector_reports_incomplete_definition`, `test_cluster_state_collector_matches_git_definition_without_metadata`, `test_cluster_state_collector_refuses_invalid_git_definition` | Met |
| C1.14 | Aura never reports an incomplete PipelineRun as succeeded | `test_tekton_inventory_collector::test_tekton_collector_never_reports_an_incomplete_run_as_succeeded` (no condition, a running condition, or another condition type is collected as `Unknown`); `test_cluster_state_diagnosis::test_cluster_state_diagnosis_model_reports_pipeline_run_status` (an `Unknown` run is reported as not completed); `test_cluster_state_collector::test_cluster_state_collector_lists_unfinished_pipeline_runs` | Met |
| C1.15 | Aura authorizes only the roles an operator supplies | `test_cli::test_cli_inventory_list_uses_only_supplied_roles`; `test_authorization::test_each_role_may_issue_only_its_command_types` | Met with caveat |
| C1.16 | Aura stores a terminal `failed` record when a record write fails | `test_cli::test_cli_inventory_list_fails_command_on_record_write_error`; `test_failure_matrix::test_each_write_failure_fails_closed` for every command scenario and write, before writing and after linking | Met |
| C1.17 | Phase 1 live validation re-run at the closure commit | [Phase 1 Closure Re-run](../validation.md#phase-1-closure-re-run), 2026-10-26 at `ea7ec7b`: every check passed; the incomplete-definition refusal had no live candidate and is covered by test | Met with caveat |

### Phase 1 Caveats

1. **C1.17.** The live re-run used the local `fleet-clusters` checkout at `4ead929`, and `fleet-clusters` at `4ead929` has no incomplete definition, so that refusal is proved by test only. The run was at `ea7ec7b`; later source changes reject only malformed input (an `oc` response without an item list, a secret-named `metadata.yaml` key, a stored record without its digest or under another key), none of which the live run met, and Git and combined inventory were re-checked live at `a236742`. `workflow inspect` and `diagnose` were not repeated.
2. **C1.8.** A model's `DiagnosisResponse` object is stored without revalidation: one built with `model_construct(confidence=5.0)` is accepted. Today's models are deterministic, in-repository, and build validated responses; revalidate before an external model is added.
3. **C1.15.** With no `--role`, the CLI authorizes `viewer`, a role the operator did not supply, as `docs/cli.md` documents; the policy itself denies an empty role set.

## Independent Grading

Closure task 5.1, 2026-10-26: an independent reviewer graded an ungraded copy of both phases at `d0053dc`, blind to these grades, probing cited tests with mutations. The stricter grade is used above wherever the reviewer's evidence was reproduced.

| # | Author | Reviewer | Used | Reason for the difference |
| --- | --- | --- | --- | --- |
| C0.3 | Met with caveat | Met | Met with caveat | The author's caveat: collectors proved with fake `oc` runners |
| C0.4 | Met with caveat | Met | Met with caveat | The author's caveat: no store-and-read-back test |
| C0.6 | Met with caveat | Met | Met with caveat | The author's caveat: the status check proves the present only |
| C1.8 | Met | Met with caveat | Met with caveat | Model response objects are not revalidated; reproduced |
| C1.15 | Met | Met with caveat | Met with caveat | The default `viewer` role; reproduced |
| C1.17 | Met with caveat | Met with caveat | Met with caveat | The reviewer added that `workflow inspect` and `diagnose` were not repeated after later source changes |

Every other row was graded the same by both.
