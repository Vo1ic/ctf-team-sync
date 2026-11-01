# Interoperability for arbitrary agent runtimes

The shared protocol describes CTF work, evidence, messages, receipts, and artifacts.
It does not prescribe a model, provider, prompt, skill system, editor, or OS.
An actor identifies a participant; a host adapter describes how a runtime receives
context. Every independent concurrent agent gets its own actor.

## Integration choices

| Runtime capability | Integration | Python on client device | Context delivery |
| --- | --- | --- | --- |
| HTTP requests | Existing `/api/v1` API and OpenAPI | No | Runtime controls event subscription and its model loop |
| stdio MCP | `team-sync-mcp` | Yes, for supplied bridge | MCP tools and cooperative event waiting |
| Local process with stdin/stdout | `team-sync-agent bridge` | Yes, for supplied bridge | JSON-RPC over JSONL, explicit delivery acknowledgements |
| Codex hooks | `hook-config --host codex` | Yes | Supported lifecycle checkpoints |
| Claude Code hooks | `hook-config --host claude-code` | Yes | Supported lifecycle checkpoints |

Runtime names and optional model/provider metadata are free text. The protocol
never needs an LLM provider API key. HTTP clients can use any language; the Node
HTTP acceptance client uses neither Python nor MCP.

No supplied adapter claims idle-session wake-up or running-tool interruption.
The application's runtime can implement scheduling if it has that capability.
`capabilities --host generic`, `--host codex`, and `--host claude-code` describe the
different contracts, not proof that a model is running.

## JSONL bridge v1

Set the usual project URL, project ID, and actor token in the environment, then run:

```sh
uv run team-sync-agent bridge
```

Each line contains a JSON-RPC 2.0 request or batch of up to 50 requests. Replies
are flushed as one JSON line. Diagnostics do not go to stdout. Frames are limited
to 2,097,152 characters. Notifications execute without a response; use IDs whenever
the outcome matters. Nested batches are invalid.

Negotiate the Team Sync protocol version and identify the runtime:

```json
{"jsonrpc":"2.0","id":1,"method":"protocol/hello","params":{"protocol_versions":[1],"client":{"name":"my-custom-agent","provider":"optional-provider","model":"any-model-name"}}}
```

The response selects version 1 and reports capabilities. Client metadata is
optional apart from `name`, remains local to the bridge connection, and does not
replace the authenticated actor. Unsupported versions fail before domain actions.

| Method | Parameters | Effect |
| --- | --- | --- |
| `protocol/hello` | protocol_versions, client | Negotiate contract and capabilities |
| `events/sync` | timeout: 0..25, default 0 | Durably cache events; acknowledge receipt |
| `context/read` | consumer, max_items?, max_chars? | Read a stable packet within the runtime's context budget |
| `context/ack` | consumer, delivery_id | Confirm host acceptance for that consumer |
| `commands/execute` | Existing AgentAction envelope | Execute the API command with its idempotency key |
| `collections/list` | collection, offset?, limit? | Read a paginated collection |
| `messages/inbox` | offset?, limit? | Read messages without changing processing state |

Context defaults to 12 items and a 6,000-character JSON items budget. A host can
request 1..50 items and 1,024..20,000 characters to fit its own model. Pending
packets remain stable; changed budgets apply after acknowledgement or replay.
The items budget excludes the small delivery envelope.

A separate `watch` process can keep the cache up to date, or the runtime can call
`events/sync` itself, including long polling. The model needs no transport logic.

## Acceptance and processing

`context/read` returns schema version, project/actor IDs, consumer, an opaque
delivery ID, cursor bounds, and data items. Until acknowledged, reads return the
same packet, including after a bridge restart. After inserting it into its context,
the host calls `context/ack` with that consumer and delivery ID. Repeated acks are
idempotent. A wrong consumer or invalidated ID cannot advance the cursor.

This confirms host acceptance, not model understanding. It never marks the server
message `processed`. The actor performs that action separately with
`commands/execute`, `operation: message.ack`, and `arguments: {"stage":"processed"}`.
Keep the domain command's idempotency key when retrying.

Use independent consumer names such as `my-runtime:<session-uuid>`. Do not mix hook
emission, legacy `context`, and explicit JSONL acknowledgements under one consumer.
The legacy `context --consumer` command still advances after stdout flush.

Revoked access blocks cached context. Processing a message elsewhere invalidates
pending packets containing it; fetch a new packet if an old ack fails. Replay also
invalidates outstanding delivery IDs. Context may truncate long bodies; complete
messages remain in the API/MCP inbox.

Authoritative schemas: [agent-bridge.schema.json](../contracts/agent-bridge.schema.json),
[delivery-batch.schema.json](../contracts/delivery-batch.schema.json), and
[existing domain commands](../contracts/commands.schema.json).

## Reference clients and acceptance

[examples/jsonl-client.mjs](../examples/jsonl-client.mjs) is an importable Node
transport with no provider SDK. Its CLI prints one packet; `--ack` explicitly
confirms delivery. A real runtime inserts data into its model's context before
acknowledging. The example itself never invokes a model.

```sh
TEAM_SYNC_PYTHON=.venv/bin/python node examples/jsonl-client.mjs
```

Reproduce network acceptance against the local Compose server:

```sh
uv run python scripts/acceptance.py --env-file .env
```

The harness creates a new, labelled acceptance CTF and preserves existing CTFs.
It runs a Python/MCP client in the project's Debian image and a standalone
Node/HTTP client in `node:22-alpine` on the server's Docker network. They exchange
a task, messages, a binary artifact, and a finding, with SHA-256 verification and
explicit message processing. Client containers are removed on exit. The generated
CTF remains for inspection; its owner credential is in a private, gitignored
`.team-sync/acceptance/<project-id>/access.json`, never in the public report.

The script discovers the running Compose image/network. For another deployment,
set `--python-image`, `--network`, `--container-url`, and `--url`. The bootstrap token
comes from the environment or the chosen local env file, never a token argument.

Tests also combine Python MCP, the Node JSONL reference runtime, plain HTTP, and
Claude-formatted hooks in one project. This validates wire interfaces and actor
isolation. A separate [live acceptance run](LIVE-ACCEPTANCE.md) passed with the
actual Claude Code CLI and two Codex collaboration subagents, including native
Claude hook delivery and independent verification. Native Codex CLI hooks,
Windows/macOS host acceptance, and two physical teammate devices require
separate runs.

Sources: [JSON-RPC](https://www.jsonrpc.org/specification),
[Claude Code hooks](https://code.claude.com/docs/en/hooks),
[Codex hooks](https://learn.chatgpt.com/docs/hooks).
