import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import StringIO
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from team_sync.delivery import AgentSettings, DeliveryError, Receiver, RemoteError, Spool

from .conftest import enroll, execute, headers
from .test_transports import live_server as live_server


@pytest.fixture
def settings(tmp_path):
    return AgentSettings(
        "http://127.0.0.1:9999", str(uuid4()), "private-test-token", tmp_path / "cache.db"
    )


def event(settings, seq, *, event_type="finding.create", actor_id=None, payload=None):
    return {
        "id": str(uuid4()),
        "project_id": settings.project_id,
        "seq": seq,
        "schema_version": 1,
        "type": event_type,
        "actor_id": actor_id or str(uuid4()),
        "payload": payload or {},
        "created_at": "2026-11-01T12:00:00Z",
    }


def test_cache_commit_rolls_back_on_sequence_gap(settings):
    spool = Spool(settings)
    with pytest.raises(DeliveryError):
        spool.ingest([event(settings, 1), event(settings, 3)], {}, [str(uuid4())], str(uuid4()))
    assert spool.cursor == 0
    assert spool.status()["events_cached"] == 0
    assert spool.pending_receipts() == []
    spool.close()


def test_resume_and_consumer_replay_are_independent(settings):
    spool = Spool(settings)
    item = event(settings, 1)
    spool.ingest([item], {1: {"kind": "change", "event": item}}, [], str(uuid4()))
    spool.close()
    spool = Spool(settings)
    assert spool.cursor == 1
    contexts, cursor = spool.context_batch("session-a")
    assert len(contexts) == 1
    spool.emitted("session-a", cursor)
    assert spool.context_batch("session-a")[0] == []
    assert len(spool.context_batch("session-b")[0]) == 1
    spool.replay("session-a")
    assert len(spool.context_batch("session-a")[0]) == 1
    spool.close()


def test_cache_binding_rejects_different_credentials(settings):
    spool = Spool(settings)
    spool.close()
    with pytest.raises(DeliveryError):
        Spool(replace(settings, token="another-private-token"))
    spool = Spool(settings)
    assert "private-test-token" not in settings.state_path.read_bytes().decode(errors="ignore")
    spool.close()


def test_context_budget_truncates_bodies_and_makes_progress(settings):
    spool = Spool(settings)
    message_id = str(uuid4())
    item = event(settings, 1)
    spool.ingest(
        [item],
        {
            1: {
                "kind": "message",
                "message": {
                    "id": message_id,
                    "body": "\x00" * 10000,
                },
            }
        },
        [],
        str(uuid4()),
    )
    contexts, cursor = spool.context_batch("session")
    assert cursor == 1
    assert contexts[0]["message"]["body_truncated"] is True
    assert len(json.dumps(contexts, ensure_ascii=False)) <= 6000
    spool.close()


def test_processed_messages_leave_pending_context(settings):
    spool = Spool(settings)
    message_id, actor_id = str(uuid4()), str(uuid4())
    spool.ingest(
        [event(settings, 1)],
        {
            1: {
                "kind": "message",
                "message": {
                    "id": message_id,
                    "body": "Review this",
                },
            }
        },
        [message_id],
        actor_id,
    )
    spool.ingest([event(settings, 2)], {}, [], actor_id, processed=[message_id])
    assert spool.context_batch("new-session")[0] == []
    assert spool.pending_receipts() == []
    spool.close()


def test_unknown_event_version_does_not_advance_cursor(settings):
    actor_id = str(uuid4())
    item = {**event(settings, 1), "schema_version": 2}

    def respond(request):
        if request.url.path.endswith("snapshot"):
            return httpx.Response(
                200, json={"project": {"id": settings.project_id}, "actor": {"id": actor_id}}
            )
        return httpx.Response(200, json={"items": [item], "next_cursor": 1, "head": 1})

    spool = Spool(settings)
    receiver = Receiver(settings, spool, transport=httpx.MockTransport(respond))
    with pytest.raises(DeliveryError, match="schema"):
        receiver.poll(0)
    assert spool.cursor == 0
    assert spool.status()["events_cached"] == 0
    receiver.close()
    spool.close()


def test_lost_receipt_response_retries_same_key_after_restart(settings):
    actor_id, author_id, message_id = str(uuid4()), str(uuid4()), str(uuid4())
    item = event(
        settings,
        1,
        event_type="message.send",
        actor_id=author_id,
        payload={"resource_id": message_id},
    )
    keys = []

    def respond(request):
        if request.url.path.endswith("snapshot"):
            return httpx.Response(
                200, json={"project": {"id": settings.project_id}, "actor": {"id": actor_id}}
            )
        if request.url.path.endswith("events/wait"):
            after = int(request.url.params["after"])
            return httpx.Response(
                200, json={"items": [item] if after == 0 else [], "next_cursor": 1, "head": 1}
            )
        if "/messages/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": message_id,
                    "body": "Team message",
                    "sender_id": author_id,
                    "recipient_id": actor_id,
                    "ack_stage": None,
                },
            )
        command = json.loads(request.content)
        keys.append(command["idempotency_key"])
        if len(keys) == 1:
            raise httpx.ReadError("Response lost after commit", request=request)
        return httpx.Response(200, json={"stage": "received"})

    spool = Spool(settings)
    receiver = Receiver(settings, spool, transport=httpx.MockTransport(respond))
    with pytest.raises(httpx.ReadError):
        receiver.poll(0)
    assert spool.cursor == 1
    assert spool.status()["receipts_pending"] == 1
    receiver.close()
    spool.close()
    spool = Spool(settings)
    receiver = Receiver(settings, spool, transport=httpx.MockTransport(respond))
    assert receiver.poll(0) == 0
    assert len(keys) == 2 and keys[0] == keys[1]
    assert spool.status()["receipts_pending"] == 0
    assert len(spool.context_batch("session")[0]) == 1
    receiver.close()
    spool.close()


def test_revocation_stops_cached_context_injection(settings):
    actor_id = str(uuid4())

    def respond(request):
        if request.url.path.endswith("snapshot"):
            return httpx.Response(
                200, json={"project": {"id": settings.project_id}, "actor": {"id": actor_id}}
            )
        return httpx.Response(401, json={"code": "invalid_credential"})

    spool = Spool(settings)
    spool.ingest(
        [event(settings, 1)], {1: {"kind": "change", "description": "Cached"}}, [], actor_id
    )
    receiver = Receiver(settings, spool, transport=httpx.MockTransport(respond))
    with pytest.raises(RemoteError):
        receiver.poll(0)
    assert spool.status()["blocked"] == "credential_revoked"
    assert spool.context_batch("session")[0] == []
    receiver.close()
    spool.close()


def test_message_resource_respects_recipient_and_project(client, project):
    first, other = enroll(client, project, "Recipient"), enroll(client, project, "Other")
    message = execute(
        client,
        project,
        "message.send",
        {
            "body": "Private content",
            "recipient_id": first["actor"]["id"],
        },
    ).json()
    endpoint = f"/api/v1/projects/{project['project']['id']}/messages/{message['id']}"
    assert client.get(endpoint, headers=headers(first["token"])).json()["body"] == "Private content"
    assert client.get(endpoint, headers=headers(other["token"])).status_code == 404
    assert client.get(endpoint).status_code == 401


def test_receiver_and_hook_processes_deliver_without_agent_polling(
    client,
    project,
    live_server,
    tmp_path,
):
    agent, other = enroll(client, project, "Receiver"), enroll(client, project, "Other")
    message = execute(
        client,
        project,
        "message.send",
        {
            "body": "Please review the shared evidence",
            "recipient_id": agent["actor"]["id"],
        },
    ).json()
    execute(
        client,
        project,
        "message.send",
        {
            "body": "Not for this receiver",
            "recipient_id": other["actor"]["id"],
        },
    )
    path = tmp_path / "receiver.sqlite3"
    environment = {
        **os.environ,
        "TEAM_SYNC_URL": live_server,
        "TEAM_SYNC_PROJECT_ID": project["project"]["id"],
        "TEAM_SYNC_TOKEN": agent["token"],
    }

    def run(*arguments, input=None):
        completed = subprocess.run(
            [sys.executable, "-m", "team_sync.agent", "--state", str(path), *arguments],
            input=input,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert agent["token"] not in completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    assert run("watch", "--once", "--timeout", "0")["receipts_pending"] == 0
    inbox = client.get(
        f"/api/v1/projects/{project['project']['id']}/inbox", headers=headers(agent["token"])
    ).json()["items"]
    assert inbox[0]["ack_stage"] == "received"
    payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": str(uuid4())})
    output = run("hook", "--host", "codex", input=payload)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "Please review the shared evidence" in context
    assert "Not for this receiver" not in context
    assert run("hook", "--host", "codex", input=payload) == {}
    execute(client, project, "message.ack", {"stage": "processed"}, message["id"], agent["token"])
    run("watch", "--once", "--timeout", "0")
    new_session = json.dumps({"hook_event_name": "SessionStart", "session_id": str(uuid4())})
    assert run("hook", "--host", "codex", input=new_session) == {}


def test_config_generation_contains_no_credentials(tmp_path):
    state_path = tmp_path / "private cache.sqlite3"
    completed = subprocess.run(
        [sys.executable, "-m", "team_sync.agent", "--state", str(state_path), "codex-config"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0
    config = json.loads(completed.stdout)
    assert set(config["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse"}
    assert "TEAM_SYNC_TOKEN" not in completed.stdout
    for groups in config["hooks"].values():
        handler = groups[0]["hooks"][0]
        assert "commandWindows" in handler
        assert handler["timeout"] == 5
        assert str(state_path) in handler["command"]


def test_hook_failure_is_nonblocking_and_does_not_echo_input():
    secret = "this-must-never-be-echoed"
    completed = subprocess.run(
        [sys.executable, "-m", "team_sync.agent", "hook", "--host", "codex"],
        input=f"not json {secret}",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0
    assert "systemMessage" in json.loads(completed.stdout)
    assert secret not in completed.stdout + completed.stderr


def test_parallel_hook_cursors_do_not_move_backwards(settings):
    spool = Spool(settings)
    items = [event(settings, seq) for seq in range(1, 4)]
    spool.ingest(
        items, {item["seq"]: {"kind": "change", "event": item} for item in items}, [], str(uuid4())
    )
    spool.close()
    barrier = Barrier(2)

    def emit(cursor):
        local = Spool(settings)
        local.context_batch("shared-session")
        barrier.wait(timeout=5)
        local.emitted("shared-session", cursor)
        local.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(emit, [1, 3]))
    spool = Spool(settings)
    assert spool.context_batch("shared-session")[0] == []
    spool.close()


def test_emission_bookkeeping_failure_keeps_valid_json_and_replay(settings, monkeypatch, capsys):
    from team_sync.agent import run_hook

    spool = Spool(settings)
    item = event(settings, 1)
    spool.ingest([item], {1: {"kind": "change", "event": item}}, [], str(uuid4()))
    monkeypatch.setattr(AgentSettings, "from_env", lambda state, access_file=None: settings)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "test-session"})),
    )

    def fail_emission(*args):
        import sqlite3

        raise sqlite3.OperationalError("Simulated bookkeeping failure")

    monkeypatch.setattr(Spool, "emitted", fail_emission)
    assert run_hook(SimpleNamespace(state=None)) == 0
    output = json.loads(capsys.readouterr().out)
    assert "hookSpecificOutput" in output
    assert len(spool.context_batch("codex:test-session")[0]) == 1
    spool.close()


def test_broken_hook_stdout_does_not_advance_cursor(settings, monkeypatch):
    from team_sync.agent import run_hook

    spool = Spool(settings)
    item = event(settings, 1)
    spool.ingest([item], {1: {"kind": "change", "event": item}}, [], str(uuid4()))

    class BrokenOutput:
        def write(self, value):
            raise BrokenPipeError

        def flush(self):
            pass

    with monkeypatch.context() as patch:
        patch.setattr(AgentSettings, "from_env", lambda state, access_file=None: settings)
        patch.setattr(
            sys,
            "stdin",
            StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "broken-session"})),
        )
        patch.setattr(sys, "stdout", BrokenOutput())
        assert run_hook(SimpleNamespace(state=None)) == 0
    assert len(spool.context_batch("codex:broken-session")[0]) == 1
    spool.close()
