# Agent delivery adapters

HTTP, MCP, and JSONL expose the same actions. A separate event receiver makes
incoming context available without asking the model to poll its inbox every step.
Host formatters support Codex and Claude Code hooks. Runtimes without hooks or MCP
use the [generic agent protocol](AGENT-PROTOCOL.md).

```text
Team Sync API → long polling → private local SQLite cache → Codex hook → model context
                                         ↓
                                  received receipt
```

## Delivery guarantees

- The receiver commits events, message bodies, its cursor, and pending receipts
  together before acknowledging receipt to the server.
- Receipt commands use stable idempotency keys. A response lost after commit can
  be retried after a receiver restart without duplicating the acknowledgement.
- Hooks read local data without network requests. A disconnected receiver can
  leave already-cached context available at the next hook checkpoint.
- `received` means the adapter persisted the message. `processed` remains an
  explicit agent/human action through the existing API/MCP command.
- Context emission is tracked per Codex session. Concurrent hooks may emit the
  same update; cursor updates cannot move backwards. This is not exactly-once
  model processing.
- The emission cursor advances after writing and flushing stdout. If the host
  dies before accepting that output, use `replay`; the server's inbox also retains
  the message and its unprocessed receipt state.
- Once the receiver observes credential revocation, cached context injection stops.
  Revocation cannot erase copies already stored on a disconnected device.
- Already processed messages are omitted during catch-up. Later processing events
  remove them from pending local context for new sessions as well.

There is **no idle-session wake-up or running-tool interruption** in this adapter.
Delivery occurs at `SessionStart`, `UserPromptSubmit`, or `PreToolUse`. A session
that is neither starting, receiving a prompt, nor calling a tool has no checkpoint.
`team-sync-agent capabilities` reports these boundaries in JSON.

## Set up the receiver

Create an agent identity in the web interface. Use the same identity for its MCP
bridge and receiver; independent agents need separate identities.

Install dependencies with `uv sync --frozen`. Set these environment variables in
both the receiver terminal and the terminal that starts Codex:

```sh
export TEAM_SYNC_URL='http://localhost:8000'
export TEAM_SYNC_PROJECT_ID='<project-uuid>'
export TEAM_SYNC_TOKEN='<agent-token>'
```

Run the receiver in a separate terminal:

```sh
uv run team-sync-agent watch
```

It reconnects with backoff after transient network failures. Invalid credentials,
incompatible event schemas, and invalid cursors stop the process with a nonzero
exit code. `--once --timeout 0` receives one batch for diagnostics.

The default cache lives under `$XDG_STATE_HOME/ctf-team-sync` or
`~/.local/state/ctf-team-sync`; Windows uses `%LOCALAPPDATA%` when available.
Its filename is scoped to the server, project, and credential. Tokens are not
stored in the database, but cached message bodies are private project data.
Do not share or commit the cache. Use `--state /absolute/path/cache.sqlite3` before
the subcommand to choose another location; `.team-sync/` is gitignored.

## Configure host hooks

Print the configuration on the device running Codex:

```sh
uv run team-sync-agent codex-config
uv run team-sync-agent hook-config --host claude-code
```

Merge the returned `hooks` entries into the CTF workspace's `.codex/hooks.json`.
For Claude Code, merge its generated `hooks` into `.claude/settings.local.json`
or an appropriate reviewed settings file. Host-specific fields stay separate.
Preserve existing hook groups. The generator writes no global or project config
and contains no credentials. It uses the current Python interpreter's absolute
path; regenerate after moving the checkout or recreating its environment. Custom
`--state` paths are carried into the generated command.

Review and trust the hooks through Codex's supported hook-trust flow. The adapter
does not bypass that flow. Keep the MCP bridge connected for reading complete
messages, claiming work, publishing results, and explicitly acknowledging processing.

The hook emits `hookSpecificOutput.additionalContext` as documented by OpenAI,
with a bounded JSON data block, provenance, and a reminder that team content does
not replace the user's objective or higher-priority instructions. Long messages
are truncated in the hook; their full text remains in the MCP inbox and local cache.
No hook blocks a tool, changes permissions, executes team message content, or
launches a model.

Sources: [OpenAI hook reference](https://learn.chatgpt.com/docs/hooks),
[Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference).

## Inspect and replay

```sh
uv run team-sync-agent status
uv run team-sync-agent context --consumer manual-review
uv run team-sync-agent replay --consumer 'codex:<session-uuid>'
```

`context` emits generic JSON for a named consumer. `replay` resets only that
consumer's emission cursor; it does not clear cached events or alter server receipts.
Unknown event schema versions stop ingestion before advancing the durable cursor.

## Validation boundary

Automated tests exercise the receiver and hook as separate processes against a
real HTTP server and PostgreSQL, including reconnect/restart behavior, lost receipt
responses, private message filtering, bounded context, parallel cursors, revocation,
and explicit processing. Hook output follows the published Codex contract.

An actual trusted Codex model session and two physical teammate devices still
need a manual acceptance run. Tests do not spend model tokens or inject messages
into the user's existing chat. Other hosts can use JSONL with explicit context
acknowledgements, plain HTTP, or a formatter for their lifecycle contract. The
Claude native binary has not been exercised as a model session in this environment.
