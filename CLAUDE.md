# Claude Instructions

Follow [`AGENTS.md`](AGENTS.md) as the authoritative repository guide.

Additional Claude-specific requirements:

- Use the workflow in [`docs/development.md`](docs/development.md) for every behavior change, including the test plan, failure-mode matrix, and independent review.
- Ask before live cluster access or external model integration.
- Do not infer repository facts from outside the provided parent directory.
- Keep answers direct, practical, and free of speculative claims.
- When a decision is uncertain, state the tradeoff and the smallest safe next step.
