import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from team_sync.agent import run_hook
from team_sync.bridge_protocol import DeliveryBatch
from team_sync.delivery import AgentSettings, DeliveryError, Spool
from team_sync.host_adapters import HOOK_HOSTS, capabilities
from team_sync.jsonl_bridge import JSONLBridge

from .conftest import enroll, headers
from .test_delivery import event
from .test_delivery import settings as settings
from .test_transports import live_server as live_server


def cached(settings):
    spool = Spool(settings)
    item = event(settings, 1)
    spool.ingest([item], {1: {"kind": "change", "event": item}}, [], str(uuid4()))
    return spool


def rpc(method, params, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def hello(bridge):
    return bridge.handle(
        rpc(
            "protocol/hello",
            {
                "protocol_versions": [1],
                "client": {
                    "name": "Unknown runtime",
                    "provider": "local",
                    "model": "any-model-name",
                },
            },
        )
    )


def test_context_delivery_repeats_until_explicit_ack_across_restart(settings):
    spool = cached(settings)
    first = spool.read_context("my-runtime")
    assert spool.status()["deliveries_pending"] == 1
    DeliveryBatch.model_validate(first)
    assert spool.context_batch("my-runtime")[0]
    spool.close()
    spool = Spool(settings)
    assert spool.read_context("my-runtime") == first
    assert spool.ack_context("my-runtime", first["delivery_id"])["already_acked"] is False
    assert spool.ack_context("my-runtime", first["delivery_id"])["already_acked"] is True
    assert spool.read_context("my-runtime")["items"] == []
    assert spool.status()["deliveries_pending"] == 0
    assert spool.context_batch("other-runtime")[0]
    spool.close()


def test_custom_runtime_can_request_a_smaller_context_budget(settings):
    spool = Spool(settings)
    spool.ingest(
        [event(settings, 1)],
        {
            1: {
                "kind": "message",
                "message": {
                    "id": str(uuid4()),
                    "body": "Large model-independent context " * 300,
                },
            }
        },
        [],
        str(uuid4()),
    )
    bridge = JSONLBridge(settings, spool)
    hello(bridge)
    request = rpc("context/read", {"consumer": "small-model", "max_items": 1, "max_chars": 1024})
    batch = bridge.handle(request)["result"]
    assert len(json.dumps(batch["items"], ensure_ascii=False)) <= 1024
    assert batch["items"][0]["message"]["body_truncated"] is True
    assert bridge.handle(request)["result"] == batch
    bridge.close()
    spool.close()


def test_delivery_ack_is_bound_to_consumer_and_invalidated_on_replay(settings):
    spool = cached(settings)
    batch = spool.read_context("one")
    with pytest.raises(DeliveryError):
        spool.ack_context("two", batch["delivery_id"])
    assert spool.context_batch("one")[0]
    spool.replay("one")
    with pytest.raises(DeliveryError):
        spool.ack_context("one", batch["delivery_id"])
    assert spool.read_context("one")["delivery_id"] != batch["delivery_id"]
    spool.close()


def test_ack_bookkeeping_is_atomic(settings, monkeypatch):
    spool = cached(settings)
    batch = spool.read_context("one")

    def fail(*args):
        raise sqlite3.OperationalError("Simulated write failure")

    monkeypatch.setattr(spool, "emitted", fail)
    with pytest.raises(sqlite3.OperationalError):
        spool.ack_context("one", batch["delivery_id"])
    assert spool.read_context("one") == batch
    spool.close()


def test_legacy_cache_upgrade_preserves_events(settings):
    spool = cached(settings)
    spool.db.execute("DROP TABLE delivery_batches")
    spool.db.execute("PRAGMA user_version=0")
    spool.close()
    spool = Spool(settings)
    assert spool.cursor == 1
    assert len(spool.read_context("upgraded")["items"]) == 1
    spool.close()


def test_pending_delivery_is_not_exposed_after_revocation(settings):
    spool = cached(settings)
    batch = spool.read_context("one")
    spool.set_meta("blocked", "credential_revoked")
    assert spool.read_context("one")["items"] == []
    with pytest.raises(DeliveryError):
        spool.ack_context("one", batch["delivery_id"])
    spool.close()


def test_processed_message_invalidates_pending_packet(settings):
    spool = Spool(settings)
    message_id = str(uuid4())
    spool.ingest(
        [event(settings, 1)],
        {1: {"kind": "message", "message": {"id": message_id, "body": "Data"}}},
        [],
        str(uuid4()),
    )
    batch = spool.read_context("one")
    spool.ingest([event(settings, 2)], {}, [], str(uuid4()), processed=[message_id])
    assert spool.read_context("one")["items"] == []
    with pytest.raises(DeliveryError):
        spool.ack_context("one", batch["delivery_id"])
    spool.close()


def test_protocol_negotiation_and_errors_do_not_require_a_known_model(settings):
    spool = cached(settings)
    bridge = JSONLBridge(settings, spool)
    assert bridge.handle(rpc("context/read", {"consumer": "any"}))["error"]["code"] == -32002
    bad = bridge.handle(
        rpc("protocol/hello", {"protocol_versions": [2], "client": {"name": "Future"}})
    )
    assert bad["error"]["code"] == -32001
    assert hello(bridge)["result"]["client"]["model"] == "any-model-name"
    batch = bridge.handle(rpc("context/read", {"consumer": "any"}))["result"]
    assert bridge.handle(rpc("context/read", {"consumer": "any"}))["result"] == batch
    assert (
        bridge.handle(rpc("context/ack", {"consumer": "any", "delivery_id": batch["delivery_id"]}))[
            "result"
        ]["already_acked"]
        is False
    )
    invalid = bridge.handle(rpc("commands/execute", {"secret": "not-for-error-output"}))
    assert invalid["error"]["code"] == -32602
    assert "not-for-error-output" not in json.dumps(invalid)
    assert bridge.handle(rpc("does/not/exist", {}))["error"]["code"] == -32601
    assert bridge.handle({"jsonrpc": "2.0", "method": "does/not/exist"}) is None
    assert bridge.handle([])["error"]["code"] == -32600
    assert bridge.handle([[rpc("context/read", {"consumer": "any"})]])[0]["error"]["code"] == -32600
    bridge.close()
    spool.close()


def test_jsonl_recovers_after_invalid_and_oversize_frames(settings, monkeypatch):
    spool = Spool(settings)
    bridge = JSONLBridge(settings, spool)
    monkeypatch.setattr("team_sync.jsonl_bridge.MAX_FRAME_CHARS", 250)
    good = json.dumps(
        rpc("protocol/hello", {"protocol_versions": [1], "client": {"name": "custom"}})
    )
    source = StringIO("bad secret-frame\n" + "x" * 400 + "\n" + good + "\n")
    sink = StringIO()
    bridge.serve(source, sink)
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [reply["error"]["code"] for reply in replies[:2]] == [-32700, -32600]
    assert replies[2]["result"]["protocol_version"] == 1
    assert "secret-frame" not in sink.getvalue()
    bridge.close()
    spool.close()


def test_host_configs_and_context_are_separate(settings, monkeypatch, capsys):
    spool = cached(settings)
    spool.close()
    monkeypatch.setattr(AgentSettings, "from_env", lambda state, access_file=None: settings)
    payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": "same-session"})
    for host in ("codex", "claude-code"):
        monkeypatch.setattr(sys, "stdin", StringIO(payload))
        assert run_hook(SimpleNamespace(state=None, host=host)) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "permissionDecision" not in json.dumps(output)
    codex = HOOK_HOSTS["codex"].config()["hooks"]["PreToolUse"][0]["hooks"][0]
    claude = HOOK_HOSTS["claude-code"].config()["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "commandWindows" in codex and "commandWindows" not in claude
    assert "additionalContextLimit" not in claude
    assert capabilities("generic")["requires_mcp"] is False


def test_mcp_node_jsonl_and_http_agents_share_one_project(client, project, live_server, tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node is required for cross-language acceptance")
    mcp_actor = enroll(client, project, "MCP agent")
    node_actor = enroll(client, project, "Custom Node runtime")
    http_actor = enroll(client, project, "HTTP plus Claude hooks")
    env = {
        **os.environ,
        "TEAM_SYNC_URL": live_server,
        "TEAM_SYNC_PROJECT_ID": project["project"]["id"],
    }

    @asynccontextmanager
    async def mcp_session():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "team_sync.mcp"],
            env={**env, "TEAM_SYNC_TOKEN": mcp_actor["token"]},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield session

    async def call(session, name, params):
        result = await session.call_tool(name, params)
        assert not result.isError, result.content
        return json.loads(result.content[0].text)

    async def scenario():
        async with mcp_session() as mcp:
            task = await call(
                mcp,
                "command",
                {
                    "action": {
                        "operation": "task.create",
                        "arguments": {"title": "Cross-runtime task"},
                        "idempotency_key": str(uuid4()),
                    }
                },
            )
            message = await call(
                mcp,
                "command",
                {
                    "action": {
                        "operation": "message.send",
                        "arguments": {
                            "recipient_id": node_actor["actor"]["id"],
                            "body": "Context from MCP",
                        },
                        "idempotency_key": str(uuid4()),
                    }
                },
            )
            source = Path(__file__).parents[1] / "web-tests/fixtures/heterogeneous-client.mjs"
            process = await asyncio.create_subprocess_exec(
                "node",
                str(source),
                env={
                    **env,
                    "TEAM_SYNC_TOKEN": node_actor["token"],
                    "TEAM_SYNC_PYTHON": sys.executable,
                    "TEST_STATE": str(tmp_path / "node.db"),
                    "TEST_TASK": task["id"],
                    "TEST_RECIPIENT": http_actor["actor"]["id"],
                },
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            output, error = await asyncio.wait_for(process.communicate(), timeout=20)
            assert process.returncode == 0, error.decode()
            report = json.loads(output)
            assert report["hello_model"] == "unlisted-local-model"
            tasks = await call(mcp, "list_items", {"collection": "tasks"})
            assert tasks["items"][0]["assignee_id"] == node_actor["actor"]["id"]
            assert tasks["items"][0]["status"] == "done"
            findings = await call(mcp, "list_items", {"collection": "findings"})
            assert findings["items"][0]["id"] == report["finding_id"]
            async with httpx.AsyncClient(base_url=live_server) as http:
                root = f"/api/v1/projects/{project['project']['id']}"
                inbox = (
                    await http.get(root + "/inbox", headers=headers(node_actor["token"]))
                ).json()
                assert inbox["items"][0]["id"] == message["id"]
                assert inbox["items"][0]["ack_stage"] == "received"
                reply = await http.post(
                    root + "/commands",
                    headers=headers(http_actor["token"]),
                    json={
                        "operation": "finding.create",
                        "arguments": {
                            "title": "HTTP result",
                            "body": "No MCP or hook needed for this write",
                            "evidence": "Plain HTTP acceptance",
                        },
                        "idempotency_key": str(uuid4()),
                    },
                )
                assert reply.status_code == 200

    asyncio.run(scenario())
    # Claude hook formatting is a third integration, independent of the model/provider.
    state_path = tmp_path / "claude.db"
    hook_env = {**env, "TEAM_SYNC_TOKEN": http_actor["token"]}
    for args, payload in [
        (["watch", "--once", "--timeout", "0"], None),
        (
            ["hook", "--host", "claude-code"],
            json.dumps({"hook_event_name": "PreToolUse", "session_id": str(uuid4())}),
        ),
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "team_sync.agent", "--state", str(state_path), *args],
            env=hook_env,
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    assert "Context from Node JSONL" in result.stdout
    assert "Context from MCP" not in result.stdout
