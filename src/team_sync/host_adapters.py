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

    def config(self, state_path=None, access_file=None):
        argv = [Path(sys.executable).as_posix(), "-m", "team_sync.agent"]
        if state_path:
            argv.extend(["--state", Path(state_path).expanduser().resolve().as_posix()])
        if access_file:
            argv.extend(["--access-file", Path(access_file).expanduser().resolve().as_posix()])
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
        "workspace_context": True,
        "resource_search": True,
        "task_handoff": "explicit_release_with_reason",
        "human_approved_artifact_review": True,
        "named_task_assignment": True,
        "expiring_activity_reports": True,
        "attention": "explicit_query" if host == "generic" else "priority_snapshot_at_checkpoint",
        "connection_file": True,
        "resource_editing": "versioned_with_history",
        "message_acknowledgements": "content_version",
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
