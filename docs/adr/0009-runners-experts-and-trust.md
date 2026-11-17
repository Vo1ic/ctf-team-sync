# ADR 0009: Runners, expert questions and the trust boundary for agent execution

## Context

Cheap agents (e.g. DeepSeek Flash) do most of the work but get stuck on images, hard
crypto and unusual output. Stronger models are expensive and should be consulted, not
run continuously. Agents must be on call without a person starting every session, and
people want to steer them from the web. An MCP session cannot be woken from outside, so
"listening to mail" needs a process outside the model.

Letting a shared server start agents on people's machines is remote code execution. A
message from another agent must stay information, never become a command.

## Decision

- **Questions are tasks** (`kind = "question"`): skills as tags, `parent_id` to the asker's
  work, leases/claims reused. `question.ask` requires `tried`, has a per-asker limit (5)
  and a duplicate check. `question.answer` creates a finding linked `leads_to` the
  parent task and sends the asker an urgent message.
- **Runner** (`team-sync-runner`) is a local process authenticated as its human owner.
  Commands exist only in its local TOML. The server stores profile names and limits
  (`runners`) and runs (`runs`: requested → approved/declined → running →
  finished/failed/cancelled); it never stores or sends a command line.
- **Approval belongs to the machine owner.** A run requested by the runner's human owner
  (from the web, or by the runner's standing local policy such as `answers_questions`)
  is approved immediately. Any other requester, human or agent, creates a `requested`
  run that only that owner can approve. `confirm_locally` adds a second click on the
  runner's local page, which does not depend on the shared server.
- **Notes are quoted.** A request note from the owner is an instruction; from anyone
  else it reaches the agent inside `<data>` with an explicit "not instructions" notice.
- **One coordinator**: at most one active coordinator run per project. Coordinators
  read `GET /board`. `flag.verify`, and forcing a flag-gated task to done, now require a
  human, so promoting an agent to coordinator never lets it decide flags.
- **Local page**: 127.0.0.1 only, random token in the URL fragment, `X-Runner-Token`
  header and Origin check on every API call (CSRF), strict CSP.
- Heartbeats are silent; only visible runner changes emit events.

## Consequences

- Prompt injection is not "solved": the guarantee is permissions (profiles, approvals,
  human-only flag/hint decisions) and process limits, not model obedience.
- A compromised shared server can still request runs, but can only choose a locally
  defined profile, and only the owner's account can approve foreign requests;
  `confirm_locally` removes even that path for sensitive profiles.
- Runs survive a runner restart only as records: a run marked running but unknown to
  the restarted runner is reported failed.
