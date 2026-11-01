"""Host-specific formatting over a host-independent receiver and delivery cache."""

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HookHost:
    name: str
    events: tuple[str, ...] = ("SessionStart", "UserPromptSubmit", "PreToolUse")

    def config(self, state_path=None):
        argv = [Path(sys.executable).as_posix(), "-m", "team_sync.agent"]
        if state_path:
            argv.extend(["--state", Path(state_path).expanduser().resolve().as_posix()])
        argv.extend(["hook", "--host", self.name])
        handler = {
            "type": "command",
            "command": shlex.join(argv),
            "timeout": 5,
            "statusMessage": "Checking team context",
        }
        if self.name == "codex":
            handler.update(
                commandWindows=subprocess.list2cmdline(argv), additionalContextLimit=3000
            )
        output = {"hooks": {event: [{"hooks": [handler.copy()]}] for event in self.events}}
        if self.name == "codex":
            output["description"] = "Team Sync context delivery from the private local cache."
        return output

    def output(self, event, context):
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


HOOK_HOSTS = {name: HookHost(name) for name in ("codex", "claude-code")}


def capabilities(host="generic"):
    return {
        "adapter": "jsonl-bridge" if host == "generic" else f"{host}-hooks",
        "schema_version": 1,
        "protocol_versions": [1],
        "durable_event_cache": True,
        "requires_mcp": False,
        "automatic_context_delivery": "caller_controlled"
        if host == "generic"
        else "lifecycle_checkpoints",
        "hook_events": [] if host == "generic" else list(HOOK_HOSTS[host].events),
        "wake_idle_session": False,
        "interrupt_running_tool": False,
        "context_confirmation": "explicit_ack" if host == "generic" else "stdout_flush",
        "marks_received": "after_local_commit",
        "marks_processed": "explicit_agent_or_human_action",
    }
