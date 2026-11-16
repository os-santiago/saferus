# Contributing

Start from an issue with observable acceptance criteria. Use a focused branch and
PR, Conventional Commits, passing quality checks and independent review. Preserve
the security boundaries in [SECURITY.md](SECURITY.md) and follow [AGENTS.md](AGENTS.md).

Run the commands in the README. Changes to installation must pass an actual
disposable kind demo; tests must include negative authorization and failure cases.
Use only synthetic data. Never commit generated state, certificates, keys, tokens
or raw audit output.

The repository contains:

- `charts/saferus/`: desired Kubernetes state and pinned upstream dependency.
- `policies/`: reviewed vault access policy.
- `cmd/console/` and `web/`: bounded synthetic demonstration application.
- `tools/`: installer, lifecycle and real environment validation.
- `tests/`: frontend and installer regression coverage.
- `docs/`: architecture, evidence and operating limits.

AI-SDLC integration accepts issues as its only external work input. Contributors
must not force internal workflow states, invoke a later stage manually, or turn a
fixture result into an autonomous delivery claim. Subsequent workflow recipes
must explicitly constrain changed paths and independent acceptance tests.
