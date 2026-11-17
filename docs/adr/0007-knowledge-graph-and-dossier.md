# ADR 0007: Knowledge graph, hypothesis lifecycle and challenge dossier

Status: accepted (0.7.0)

## Context

Hard challenges are solved by testing many hypotheses. Findings were a flat list, so
a refuted idea, the reason it was refuted and the path to the flag lived only in the
heads (or contexts) of whoever worked on it. Files could only be referenced by URL in
text. After the CTF nobody had a complete writeup.

## Decision

- A project-scoped `links` table stores typed edges between challenges, tasks,
  findings, flags and artifacts. Each side has a typed composite foreign key column
  (as `resource_revisions` does) plus check constraints, so integrity stays in the DB.
- Relations are deliberately few: `attached_to`, `leads_to`, `supports`, `refutes`,
  `supersedes`. Edges never modify or delete their endpoints; deleting an edge is an
  explicit command by its author or a coordinator.
- Hypotheses carry `status` (`open/confirmed/refuted`). A `refutes` edge or a `supports`
  edge from a `result` changes it through a normal revision with provenance.
- Attachments reuse immutable artifacts (SHA-256, review rules unchanged) and add
  `attached_to` edges; there is no second file store.
- The dossier is generated on read from current state (never stale) and adds one
  editable field, `challenges.writeup`. It is Markdown with a Mermaid graph so it can
  be exported as the team's writeup; the web UI renders the same graph as SVG.

## Consequences

Migration `d7a3c95e1b42` adds a table and two columns; old clients are unaffected.
Findings with `task_id` already inherit the task's challenge (0.6), so the dossier
collects everything for a challenge without extra input.
