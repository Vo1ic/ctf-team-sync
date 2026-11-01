# Heterogeneous agent delivery and explicit host confirmation

Status: accepted, 2026-11-01.

## Context

Participants use different models, frameworks, skills, operating systems, and
hosts. HTTP, MCP, and hook support vary independently. A Codex hook alone cannot
serve as the universal team communication contract.

## Decision

The existing project-scoped domain API remains authoritative. Host names and
models introduce no server dispatch rules. HTTP-only clients are first-class.
MCP and JSONL adapt those same operations.

The receiver/cache are independent of host formatting. Codex and Claude Code use
distinct configuration renderers and consumer namespaces. Generic JSONL negotiates
its version and accepts arbitrary runtime/provider/model metadata.

Explicit delivery uses persistent, opaque IDs. Reading does not advance a cursor.
Acknowledging host acceptance commits the cursor atomically and is idempotent.
Host acceptance, adapter receipt, and message processing remain distinct. No
supplied adapter claims arbitrary model wake-up.

Local cache changes are additive: legacy caches retain events, cursors, and the
receipt outbox. `codex-config` and stdout-confirmed `context` remain available.
`capabilities` now describes the generic profile by default; use `--host codex`
for the previous host description. New wire schemas are versioned separately
from the unchanged HTTP `/api/v1` API.

## Consequences

Custom frameworks need no provider-specific dependency. They decide how to insert
data and confirm processing. Hooks retain weaker stdout-emission confirmation.
Native model and physical-device acceptance remain separate from protocol tests.
