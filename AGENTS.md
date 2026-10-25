# Agent Instructions

These instructions apply to all coding and documentation agents working in this repository.

## Mission

Build Aura as a read-only sidecar control plane for OpenShift Partner Labs. Preserve the existing lifecycle authorities:

- Git for desired state.
- OpenShift for actual state.
- Tekton for workflow execution.
- Portal for operator-facing request data.

Do not replace RabbitMQ and do not turn Aura into a workflow engine.

## Hard Boundaries

Never modify:

- `fleet`
- `fleet-cluster-scaffold`
- `fleet-clusters`
- `portal`

Do not:

- Execute pipelines.
- Generate or apply patches without a future approved guard.
- Write Portal data.
- Send secrets or cluster-derived evidence to an external model.
- Bypass CWA for a model call.
- Treat model output as authorization.

Read only inside the provided parent repository unless the user explicitly grants another path.

## Development Process

Follow [`docs/development.md`](docs/development.md) for every behavior change. In summary:

1. Read the governing spec and contract docs; ask the operator when behavior is unspecified.
2. Write a test plan before any test or code, covering behavior, contract negatives, failure modes, invariants, and input edges.
3. Write failing tests and confirm each fails for the right reason.
4. Make the smallest correct implementation.
5. Run the focused tests, the full suite, `git diff --check`, and the quality gate, all before the commit is final.
6. Update affected docs in the same commit series.
7. Create an incremental conventional commit.
8. Get an independent review of each feature, reproduce every finding, and fix confirmed findings test-first.
9. Run the CI workflow locally with `act` on the committed tree before reporting the feature ready to publish.

Useful commands:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
git diff --check
ripwire . --quality-delta=adb2c2b..HEAD
DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')" act push -j test -P ubuntu-latest=catthehacker/ubuntu:act-latest --container-daemon-socket - --pull=false
```

Run live cluster validation only when explicitly approved by the operator. By default, give the operator the commands and expected results to run.

## Commit Rules

- Use Conventional Commits.
- Use `git commit -s`.
- Keep each commit independently meaningful.
- Do not commit unrelated local artifacts.
- Do not amend or rewrite pushed history unless explicitly requested.

Examples:

```text
feat(inventory): classify archived clusters
fix(workflow): read tekton cluster parameter
docs(phase-1): record validation
```

## Code Style

- Match the existing Python style; no formatter is configured.
- Prefer small typed classes and explicit contracts.
- Keep collector logic deterministic.
- Keep authorization outside collectors and models.
- Keep stores immutable and append-only.
- Do not add inline comments unless they explain a non-obvious invariant.

## Documentation Rules

- Use lowercase filenames under `docs/`; retain the conventional uppercase root filenames `README.md`, `AGENTS.md`, and `CLAUDE.md`.
- Update `docs/cli.md` when CLI flags or defaults change.
- Update `docs/evidence.md` when evidence, CWA, audit, or diagnosis contracts change.
- Update `docs/wiring.md` when command wiring changes.
- Update `docs/validation.md` when validation procedures or expected results change.
- Update `docs/development.md` when the workflow, project invariants, or failure-mode matrix change.
- Draw diagrams for human readers, such as those in `README.md`, as SVG files under `docs/assets/` that render in light and dark mode, with descriptive alt text.
- Use raw Mermaid blocks for diagrams in agent guidance and reference docs under `docs/`.

## Local Artifacts

Leave these untracked local artifacts alone unless the user explicitly asks otherwise:

- `.codex/`
- `.idea/`
- `.mcp.json`
- `.serena/`

## Test Guidance

- Collector tests use fake clients and local temporary Git trees.
- Correlation tests that depend on Git item shapes use the real `GitInventoryCollector` over a temporary Git tree, not hand-built items.
- CLI tests cover argument validation and JSON output.
- Contract tests use Pydantic validation.
- Store tests assert exclusive-create behavior.
- Fixture tests must not contain secrets.
- Every spec rule that says only, exactly, empty, must, must not, or never has a negative test against the contract or validator, not only the generator.
- Every command has failure tests for each record write and external read in its failure-mode matrix, asserting the terminal record, absent artifacts, and the `command_failed` link. Add a `Scenario` to `tests/test_failure_matrix.py` for each new command or outcome, with its documented write order; the shared harness then tests every row.
- Tests force failures deterministically, for example by pre-creating a record at its deterministic ID, and restore any permissions they change.
- Tests never rely on file modes to make a path unreadable, because root ignores them; use `deny_access` from `tests/failure_injection.py`.
- A change that touches a project invariant in `docs/development.md` has a test asserting it.
- A collector that reads a new Kubernetes resource adds a `list`-only grant to `deploy/`; `tests/test_deploy_manifests.py` enforces it.

## Security Guidance

- Keep the service account read-only.
- Keep the kubeconfig isolated from administrator credentials.
- Add redaction tests when new evidence fields can contain credentials.
- External model integration requires explicit operator approval and a data-flow review.

## Definition of Done

- A test plan covering all five classes in `docs/development.md` existed before the tests.
- Every spec rule the change implements maps to at least one test.
- Full test suite passes.
- Diff hygiene passes.
- The quality gate reports zero gating findings; only confirmed false positives are acknowledged, with a reason.
- Affected documentation is current.
- Protected repositories have no tracked changes.
- The change is committed with a conventional commit and sign-off.
- The feature has had an independent review, and every confirmed finding is fixed test-first.
- The CI workflow passes locally under `act` on the committed tree.

A completed task or checklist item does not mean the work is done; this list does.
