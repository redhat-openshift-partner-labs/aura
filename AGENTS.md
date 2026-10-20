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

1. Write a failing test.
2. Make the smallest correct implementation.
3. Run the focused test.
4. Run the full suite.
5. Run `git diff --check`.
6. Create an incremental conventional commit.

Useful commands:

```bash
UV_CACHE_DIR=/private/tmp/aura-uv-cache uv run --no-sync pytest
git diff --check
```

Run live cluster validation only when explicitly approved by the operator.

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
- Use raw Mermaid blocks for diagrams.

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

## Security Guidance

- Keep the service account read-only.
- Keep the kubeconfig isolated from administrator credentials.
- Add redaction tests when new evidence fields can contain credentials.
- External model integration requires explicit operator approval and a data-flow review.

## Definition of Done

- Full test suite passes.
- Diff hygiene passes.
- Affected documentation is current.
- Protected repositories have no tracked changes.
- The change is committed with a conventional commit and sign-off.
