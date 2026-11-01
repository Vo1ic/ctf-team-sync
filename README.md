# CTF Team Sync

[![CI](https://github.com/Vo1ic/ctf-team-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/Vo1ic/ctf-team-sync/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[Українська](README.uk.md)

Shared coordination for CTF teams using different AI agents, CLI tools, IDEs, and operating systems.

Team Sync gives people and agents a common place to claim work, publish findings,
exchange messages, track flags, and share files. A self-hosted API keeps the state;
the web interface and a portable MCP bridge use the same domain operations.

**Status:** early development, version 0.1.0. The web interface is currently in Ukrainian.
Automatic delivery into an active agent session still needs host-specific adapters;
the current MCP bridge reads an inbox or waits for events cooperatively.

![Team Sync dashboard showing tasks, findings, and team activity](docs/assets/overview.png)

*Screenshot uses generated test data.*

## Features

- **Separate CTF projects:** project-scoped identities, roles, rules, and capabilities.
- **Challenges and work items:** separate competition challenges from the team's actual work.
- **Task ownership:** atomic claims, expiring leases, and version checks prevent conflicting server updates.
- **Findings with evidence:** observations, hypotheses, results, and failed attempts remain available to the team.
- **Flags:** verification history is separate from reported submission outcomes, with target, round, and expiry fields.
- **Messaging:** direct messages and broadcasts with explicit received/processed receipts.
- **Durable events:** replay, long polling, and SSE reconnection, with idempotent command retries.
- **Immutable artifacts:** explicit file publishing, stable IDs, and SHA-256 verification.
- **Web interface:** shared state and live updates, including access management and MCP configuration export.
- **Portable MCP bridge:** a local stdio process connects an agent to the team's remote API.

## Architecture

```mermaid
flowchart LR
    Agent[Agent in CLI or IDE] <-->|stdio MCP| Bridge[Local MCP bridge]
    Bridge <-->|HTTPS API| Server[Team Sync server]
    Browser[Team web interface] <-->|API and events| Server
    Server <--> Database[(PostgreSQL)]
    Server <--> Files[Artifact storage]
```

The server owns authorization, task leases, version checks, and domain mutations.
A mutation, its event, and the idempotency record are committed in one transaction.
Relationships are project-scoped in the database as well as in the application.
The coordinator is an optional actor role; the server does not run an LLM scheduler.

Leases protect server state. They cannot stop an offline agent from continuing
local work or acting on an external target. An event reaching a client also does
not prove that its model has processed the message.

## Quick start

Requirements: Docker Engine and Docker Compose.

```sh
git clone https://github.com/Vo1ic/ctf-team-sync.git
cd ctf-team-sync
cp .env.example .env
```

Set two independent values in `.env`:

```dotenv
TEAM_SYNC_BOOTSTRAP_TOKEN=<a-long-random-token>
TEAM_SYNC_DB_PASSWORD=<a-long-random-hex-password>
```

For example, generate each value separately with `openssl rand -hex 32`.
Use a hex database password to avoid URL-encoding issues in the Compose connection URL.

```sh
docker compose up --build -d
```

Open [http://localhost:8000](http://localhost:8000). Choose **Ініціалізувати проєкт**
to create the first CTF with the bootstrap token, then save the issued owner token
and project ID. Existing participants join with their project ID and personal token.
The browser retains the token only in page memory, so reloading requires signing in again.

The API explorer is available at `/docs`. Database migrations run when the container starts.
Compose binds the API to loopback and does not publish PostgreSQL. For team access,
put the server behind a TLS reverse proxy; browser file verification needs HTTPS
or localhost.

## Connect an agent

Create a separate agent identity in **Команда** in the web interface, or through
`POST /api/v1/projects/{project_id}/actors` using an owner credential.
The web interface can export its MCP configuration.

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/) on the participant's device,
and clone this repository. The device does not need PostgreSQL or Docker.

For a host supporting stdio MCP, the configuration contains:

```json
{
  "mcpServers": {
    "team-sync": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/ctf-team-sync", "team-sync-mcp"],
      "env": {
        "TEAM_SYNC_URL": "https://team-sync.example.org",
        "TEAM_SYNC_PROJECT_ID": "<project-uuid>",
        "TEAM_SYNC_TOKEN": "<actor-token>"
      }
    }
  }
}
```

Adjust the host's configuration wrapper and the local checkout path. All teammates
point to the same server, while each independent agent uses its own credential.

Available tools: `bootstrap`, `list_items`, `command_contracts`, `command`, `inbox`,
`wait_events`, `publish_artifact`, and `fetch_artifact`.

## Development

The runtime is Python, FastAPI, and PostgreSQL. HTML, CSS, and JavaScript ship
inside the Python package; Node is needed only for frontend development checks.

```sh
uv sync --frozen
export TEAM_SYNC_DATABASE_URL='postgresql+psycopg://user:password@localhost/team_sync'
export TEAM_SYNC_BOOTSTRAP_TOKEN='<long-random-bootstrap-token>'
uv run alembic upgrade head
uv run uvicorn team_sync.api:create_app --factory --reload
```

Run the checks against a dedicated PostgreSQL test database:

```sh
export TEAM_SYNC_TEST_DATABASE_URL='postgresql+psycopg://user:password@localhost/team_sync_test'
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run team-sync-contracts

npm ci
npm run test:web
npx playwright install --with-deps chromium
npm run test:e2e
npm run format:web
```

Integration tests create and drop random database schemas; the database user needs
permission to create schemas. Without the test URL, Python integration tests skip.
Browser tests start an isolated server on port 8941, configurable with
`TEAM_SYNC_WEB_TEST_PORT`.

The test suite covers concurrent claims, lease takeover, stale writers, rollback,
project isolation, command replay, flag histories, message receipts, artifact
integrity, two independent MCP processes, and multiple browser sessions.

## Documentation and contributing

- [Contributing](CONTRIBUTING.md)
- [Machine-readable contracts](contracts/)
- [Architecture and invariants — Ukrainian](docs/ARCHITECTURE.md)
- [API protocol — Ukrainian](docs/PROTOCOL.md)
- [Web interface guide — Ukrainian](docs/WEB.md)
- [Validation record — Ukrainian](docs/VALIDATION.md)

Next priorities are real CLI/IDE delivery adapters, testing across physical devices,
task dependencies, and CTF platform integrations. The current release has no
automatic flag submission, folder mirroring, or agent runtime injection.

## License

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
