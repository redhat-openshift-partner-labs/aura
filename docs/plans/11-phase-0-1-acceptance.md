# Phases 0 and 1 Acceptance

## Status

Draft for operator sign-off. The Phase 0 part was graded by the author on 2026-10-25 at Aura commit `df58f13`; the Phase 1 part is task 3.1 of the [closure plan](10-phase-0-2-closure.md). Stage 5 of that plan has an independent reviewer grade an ungraded copy blind, and the stricter grade is kept once its evidence is reproduced.

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
| C0.2 | A read-only Git inventory collector returns typed cluster data | `test_git_inventory_collector::test_git_inventory_collector_returns_typed_evidence`, `test_git_inventory_collector_lists_active_and_archived_clusters`, `test_git_inventory_collector_reads_head_not_working_tree`, `test_git_inventory_collector_ignores_an_inherited_git_dir`; Git runs only through `aura.process` with `isolated_git_environment()` (`test_architecture::test_only_the_process_module_imports_subprocess`) | Met |
| C0.3 | A read-only OpenShift collector returns typed cluster and PipelineRun status | `test_openshift_inventory_collector::test_openshift_inventory_collector_returns_cluster_actual_state`, `test_openshift_inventory_collector_reports_missing_managed_cluster`; `test_tekton_inventory_collector::test_tekton_collector_returns_pipeline_run_with_failed_task`; `test_openshift_cli_client::test_openshift_cli_client_reads_inventory`; `test_tekton_cli_client::test_openshift_cli_tekton_client_reads_workflow_status`; the clients issue only `oc get`, and `test_deploy_manifests::test_manifests_create_only_read_only_access_objects` keeps the service account list-only | Met |
| C0.4 | Redaction removes known secret fields before evidence storage | Every command's snapshot passes through `redact_snapshot` in the shared evidence service (`src/aura/services/evidence.py`) before the CLI stores it: `test_inventory_service::test_inventory_service_redacts_collected_evidence`; rules in `test_redaction::test_redacts_default_secret_fields_recursively`, `test_redacts_secret_field_spelling_variants`, `test_redacts_parameters_with_secret_terms`; collector-side redactions kept: `test_tekton_inventory_collector::test_tekton_collector_records_redacted_params_and_workspaces`, `test_combined_collectors_keep_included_tekton_redactions`; `test_fixtures::test_fleet_clusters_inventory_fixture_contains_no_secrets` | Met with caveat |
| C0.5 | Authorization rules reject unauthorized command types | `test_authorization::test_viewer_cannot_issue_mutating_commands`, `test_proposer_cannot_execute`, `test_viewer_cannot_approve`, `test_empty_roles_are_denied`; service refusals `test_inventory_service::test_inventory_service_rejects_unauthorized_operator`, `test_workflow_service::test_workflow_service_rejects_unauthorized_operator`, `test_cluster_state_service::test_cluster_state_service_rejects_unauthorized_operator`; CLI refusals `test_cli::test_cli_inventory_list_uses_only_supplied_roles`, `test_cli_propose::test_cli_propose_deprovision_requires_proposer_or_admin`, `test_cli_scaffold::test_cli_scaffold_requires_proposer_or_admin` | Met |
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

1. **C0.4.** Redaction is proved at the shared evidence service and the CLI stores the snapshot that service returns, but no single test stores a snapshot built from secret-bearing evidence and reads the stored file back.
2. **C0.6.** The status check shows the protected checkouts unchanged at grading time; it does not prove that no earlier run wrote to them. Aura's only Git writes go to temporary clones, as the cited tests show.

## Phase 1

Graded in closure task 3.1.
