# ADR 0008: Verified completion, human-approved hints, decoys and stalled work

Status: accepted (0.8.0)

## Context

During CTF Lviv 2026 tasks were marked done with unverified or wrong flags, a paid
hint was opened without permission, decoys (prompt injections, planted values) fooled
several agents, and nobody noticed agents that held a lease without progress.

## Decision

- New task status `review`. `task.complete` on a challenge task (flags enabled) moves it
  to `review` unless a confirmed flag already exists; `without_flag=true` is for helper
  work. Confirming a flag of the challenge finishes the waiting tasks; rejecting the last
  candidate reopens them and nominates the previous assignee. Non-leads cannot set
  `done` on such tasks through the generic editor. Solved = a human-confirmed flag.
- `hint_requests`: `hint.request` requires at least one own finding on the challenge
  (proof of what was tested), notifies human leads urgently; `hint.decide` is human-lead
  only; `hint.used` records the hint text as a finding. The server cannot see the CTF
  platform, so this is a workflow gate plus an auditable ledger, not a technical lock.
- Finding kind `decoy`; scoped agent context lists `known_decoys`, the dossier shows them.
- `GET /stalled?minutes=20`: live leases without findings/logs since claim, or with a
  blocked/waiting assignee — computed on read, no schema.
- Web: URL routes for sections and records, back navigation between cards, cards reopen
  after a form, clickable references everywhere, keyboard shortcuts.

## Compatibility

Migration `e5b81f0c7d23` renames the task status check to `task_status` and adds
`review`; downgrade maps `review` back to `done`. Clients that only knew
open/running/done should treat unknown statuses as not claimable.
