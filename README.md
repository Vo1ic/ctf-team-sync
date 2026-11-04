# CTF Team Sync

[![CI](https://github.com/Vo1ic/ctf-team-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/Vo1ic/ctf-team-sync/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[Українська](README.uk.md)

Shared coordination for CTF teams using different AI agents, CLI tools, IDEs, and operating systems.

Team Sync gives people and agents a common place to claim work, publish findings,
exchange messages, track flags, and share files. A self-hosted API keeps the state;
the web interface and a portable MCP bridge use the same domain operations.

**Status:** early development, version 0.1.0. The web interface is currently in Ukrainian.
A shared receiver supports Codex hooks, Claude Code hooks, and a generic JSONL
bridge with explicit context acknowledgement. HTTP-only agents need no Python
or MCP. Host adapters deliver at supported checkpoints and do not wake idle sessions.

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
- **Account-free web access:** open a project link or connection file; browser access persists across refresh, with separate identities per tab.
- **Agent workspace:** bounded personal context, search, full resource lookup, typed MCP actions, and immediate task handoffs with preserved evidence.
- **Sent-message receipts:** see explicit recipient delivery/processing stages without exposing other private conversations.
- **Portable MCP bridge:** a local stdio process connects an agent to the team's remote API.
- **Heterogeneous agents:** HTTP, MCP, and JSONL share one domain protocol; Codex and Claude Code hooks are optional host adapters.

## Architecture

```mermaid
flowchart LR
    Agent[Agent in CLI or IDE] <-->|stdio MCP| Bridge[Local MCP bridge]
    Bridge <-->|HTTPS API| Server[Team Sync server]
    Custom[Custom runtime] <-->|JSONL| JSONL[Generic bridge]
    JSONL <-->|HTTPS API| Server
    HTTP[HTTP-only agent] <-->|HTTPS API| Server
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

For source editing, use the development override with the same database and files:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

Python changes trigger server reload. HTML/CSS/JS changes appear after refreshing
the browser; they do not need an image rebuild. Rebuild after dependency changes.
Return to the normal image with `docker compose up --build -d`.

## Connect an agent

Create a separate agent identity in **Команда** in the web interface, or through
`POST /api/v1/projects/{project_id}/actors` using an owner credential.
The web interface can export its MCP configuration.

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/) on the participant's device,
The exported default uses uvx with the versioned Git package: no checkout is needed.
The device does not need PostgreSQL or Docker. A local checkout remains an alternative.

For a host supporting stdio MCP, the configuration contains:

```json
{
  "mcpServers": {
    "team-sync": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Vo1ic/ctf-team-sync@v0.4.0", "team-sync-mcp"],
      "env": {
        "TEAM_SYNC_URL": "https://team-sync.example.org",
        "TEAM_SYNC_PROJECT_ID": "<project-uuid>",
        "TEAM_SYNC_TOKEN": "<actor-token>"
      }
    }
  }
}
```

Adjust the host's configuration wrapper; uvx must be on the client's PATH. All teammates
point to the same server, while each independent agent uses its own credential.

Start with `get_context`. Use `search`, `get_resource`, `update_task`,
`report_finding`, `send_message`, `acknowledge_message`, and `outbox` for daily work.
Use `get_attention` for prioritized pending decisions and `report_activity` for an
honest, expiring status. Tasks can nominate people or agents without pretending
that they have accepted the work.
Legacy bootstrap/command tools remain supported. Read the [agent workflow](docs/AGENT-WORKFLOW.md).
A private connection file can replace three environment variables with `TEAM_SYNC_ACCESS_FILE`.

In **Файли**, humans can upload a file/photo or paste text. An agent takes the
review and asks for permission; a human approves or declines beside the artifact.
Protected bytes unlock after approval and an explicit start. The agent publishes
a README with authorship. Unusable material is archived with a reason and remains
recoverable by a human. Analysis runs in the connected agent's CLI/IDE.
See [human workflows and attention](docs/adr/0003-human-reviews-and-agent-attention.md).

Every working record has **Редагувати** and **Історія правок** where your role
permits it. Agents use `get_edit_contract`, `edit_resource`, and `get_edit_history`
over the same API; JSONL exposes the same commands. Edits preserve authorship and
before/after history, reject concurrent overwrites, and require new acknowledgements
for edited messages. File/text changes publish linked immutable versions.
See [versioned editing](docs/adr/0004-versioned-resource-editing.md).

For context delivery without agent-initiated polling, see [Agent adapters](docs/ADAPTERS.md).
The receiver runs with `uv run team-sync-agent watch`; `codex-config` prints hook
configuration for review and merging, without modifying existing Codex settings.

For runtimes without MCP or hooks, see the [host-independent protocol](docs/AGENT-PROTOCOL.md)
and [Node reference client](examples/jsonl-client.mjs). Client/provider/model names
are open metadata. A runtime can choose its context budget and explicitly confirm
delivery without automatically marking messages processed.

```sh
uv run team-sync-agent bridge
uv run team-sync-agent hook-config --host claude-code
uv run team-sync-agent capabilities --host generic
```

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
- [Agent workflow and context tools — Ukrainian](docs/AGENT-WORKFLOW.md)
- [Agent delivery adapters](docs/ADAPTERS.md)
- [Host-independent protocol and network acceptance](docs/AGENT-PROTOCOL.md)
- [Live Claude Code and Codex acceptance — Ukrainian](docs/LIVE-ACCEPTANCE.md)
- [Machine-readable contracts](contracts/)
- [Architecture and invariants — Ukrainian](docs/ARCHITECTURE.md)
- [API protocol — Ukrainian](docs/PROTOCOL.md)
- [Web interface guide — Ukrainian](docs/WEB.md)
- [Populated demo and simulated live activity — Ukrainian](docs/DEMO.md)
- [Validation record — Ukrainian](docs/VALIDATION.md)

Next priorities are native Codex CLI hook acceptance, more CLI/IDE adapters, testing across physical devices,
task dependencies, and CTF platform integrations. The current release has no
automatic flag submission, folder mirroring, or universal idle-session wake-up.

## License

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
