# Contributing

Team Sync is in early development. Issues and pull requests are welcome.
Please describe the problem and expected behavior before introducing a new
service, host adapter, or domain concept.

## Architectural boundaries

- Every domain resource belongs to one CTF project. Related records need
  project-scoped composite foreign keys.
- All transports use the same server-side domain operations in `Core`.
- A mutation, its event, and its idempotency record belong to one transaction.
- Task claims and completion preserve lease ownership and version checks.
- Findings, flag checks, and submission reports retain authorship and evidence.
- Published artifacts are immutable; local paths are not shared identifiers.
- Host adapters must describe their actual delivery capability and fallback.

Read [AGENTS.md](AGENTS.md) and the [architecture](docs/ARCHITECTURE.md) before
changing these rules. Explain incompatible changes in an architecture decision
record, provide migrations, and update the exported contracts.

## Setup and checks

Follow the development section in [README.md](README.md). Use a dedicated
PostgreSQL database for integration tests; tests create and drop their own schemas.

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
npm ci
npm run test:web
npm run test:e2e
npm run format:web
```

Set `TEAM_SYNC_TEST_DATABASE_URL` and install the Playwright Chromium browser
before running integration and browser tests. Regenerate contract changes with
`uv run team-sync-contracts` and include the resulting files.

Keep pull requests focused. Include the concrete behavior change and relevant
validation. Do not commit credentials, runtime data, CTF flags, private challenge
materials, exported MCP configurations, or browser test artifacts.

Contributions are made under the repository's Apache-2.0 license.
