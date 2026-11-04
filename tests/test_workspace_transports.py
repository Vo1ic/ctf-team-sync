import asyncio
import json
import os
import sys
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from team_sync.delivery import AgentSettings, Spool
from team_sync.jsonl_bridge import JSONLBridge

from .conftest import enroll, execute
from .test_human_workflows import upload
from .test_transports import live_server as live_server


def test_native_mcp_human_review_report_and_jsonl_attention(client, project, live_server, tmp_path):
    agent = enroll(client, project, "Kira")
    artifact = upload(client, project)
    settings = AgentSettings(
        live_server, project["project"]["id"], agent["token"], tmp_path / "cache.db"
    )
    spool = Spool(settings)
    bridge = JSONLBridge(settings, spool)
    bridge.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "protocol/hello",
            "params": {"protocol_versions": [1], "client": {"name": "Different CLI"}},
        }
    )

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "team_sync.mcp"],
            env={
                **os.environ,
                "TEAM_SYNC_URL": live_server,
                "TEAM_SYNC_PROJECT_ID": project["project"]["id"],
                "TEAM_SYNC_TOKEN": agent["token"],
                "TEAM_SYNC_ACCESS_FILE": "",
            },
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()

                async def call(name, **args):
                    response = await session.call_tool(name, args)
                    assert not response.isError, response.content
                    return json.loads(response.content[0].text)

                task = await call(
                    "request_artifact_review",
                    artifact_id=artifact["id"],
                    idempotency_key=str(uuid4()),
                )
                denied = await session.call_tool(
                    "fetch_artifact",
                    {"artifact_id": artifact["id"], "local_path": str(tmp_path / "denied.txt")},
                )
                assert denied.isError and not (tmp_path / "denied.txt").exists()
                approved = execute(
                    client,
                    project,
                    "artifact.review.decide",
                    {"expected_version": task["version"], "decision": "approved"},
                    task["id"],
                ).json()
                attention = bridge.handle(
                    {"jsonrpc": "2.0", "id": 2, "method": "messages/attention", "params": {}}
                )
                assert attention["result"]["items"][0]["kind"] == "approval"
                task = await call(
                    "update_artifact_review",
                    task_id=task["id"],
                    operation="start",
                    expected_version=approved["version"],
                    idempotency_key=str(uuid4()),
                )
                await call(
                    "fetch_artifact",
                    artifact_id=artifact["id"],
                    local_path=str(tmp_path / "approved.txt"),
                )
                assert (tmp_path / "approved.txt").read_bytes() == b"Synthetic CTF evidence"
                done = await call(
                    "update_artifact_review",
                    task_id=task["id"],
                    operation="complete",
                    expected_version=task["version"],
                    readme="# README\nSynthetic analysis.",
                    idempotency_key=str(uuid4()),
                )
                assert done["status"] == "done"

    try:
        asyncio.run(scenario())
    finally:
        bridge.close()
        spool.close()


def test_mcp_connection_file_and_jsonl_share_context_handoffs_and_receipts(
    client, project, live_server, tmp_path
):
    first, second = enroll(client, project, "MCP worker"), enroll(client, project, "JSONL worker")
    task = execute(client, project, "task.create", {"title": "Portable handoff"}).json()

    def access(actor, name):
        path = tmp_path / name
        path.write_text(
            json.dumps(
                {
                    "url": live_server,
                    "project_id": project["project"]["id"],
                    "token": actor["token"],
                }
            )
        )
        return path

    mcp_file = access(first, "mcp.json")
    settings = AgentSettings.from_env(tmp_path / "cache.db", access(second, "jsonl.json"))
    spool = Spool(settings)
    bridge = JSONLBridge(settings, spool)

    def rpc(method, params):
        result = bridge.handle(
            {"jsonrpc": "2.0", "id": str(uuid4()), "method": method, "params": params}
        )
        assert "error" not in result, result
        return result["result"]

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "team_sync.mcp"],
            env={
                **os.environ,
                "TEAM_SYNC_ACCESS_FILE": str(mcp_file),
                "TEAM_SYNC_URL": "https://stale.invalid",
            },
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as mcp:
                await mcp.initialize()

                async def call(name, args=None):
                    response = await mcp.call_tool(name, args or {})
                    assert not response.isError, response.content
                    return json.loads(response.content[0].text)

                tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
                assert tools["get_context"].annotations.readOnlyHint
                packet = await mcp.call_tool("get_context", {"max_chars": 4000})
                assert not packet.isError and len(packet.content[0].text) <= 4000
                context = json.loads(packet.content[0].text)
                assert context["self"]["id"] == first["actor"]["id"]
                assert context["sections"]["available_tasks"]["items"][0]["id"] == task["id"]
                schema = await call("command_contracts", {"operation": "task.release"})
                assert "reason" in schema["properties"] and "task.create" not in schema
                claimed = await call(
                    "update_task",
                    {
                        "operation": "claim",
                        "task_id": task["id"],
                        "expected_version": 1,
                        "idempotency_key": str(uuid4()),
                    },
                )
                await call(
                    "report_finding",
                    {
                        "title": "Partial evidence",
                        "body": "Read the header",
                        "evidence": "Synthetic fixture",
                        "task_id": task["id"],
                        "idempotency_key": str(uuid4()),
                    },
                )
                released = await call(
                    "update_task",
                    {
                        "operation": "release",
                        "task_id": task["id"],
                        "expected_version": claimed["version"],
                        "reason": "Resume from marker: header parsed",
                        "idempotency_key": str(uuid4()),
                    },
                )
                assert released["status"] == "open"
                rpc(
                    "protocol/hello",
                    {"protocol_versions": [1], "client": {"name": "Arbitrary host"}},
                )
                context = rpc("workspace/context", {"task_id": task["id"]})
                assert context["self"]["id"] == second["actor"]["id"]
                detail = rpc("resources/get", {"collection": "tasks", "resource_id": task["id"]})
                assert detail["related"]["findings"]["total"] == 2
                assert rpc("resources/search", {"query": "Resume from marker"})["total"] == 1
                claimed = rpc(
                    "commands/execute",
                    {
                        "operation": "task.claim",
                        "resource_id": task["id"],
                        "arguments": {"expected_version": released["version"]},
                        "idempotency_key": str(uuid4()),
                    },
                )
                stale = await mcp.call_tool(
                    "update_task",
                    {
                        "operation": "complete",
                        "task_id": task["id"],
                        "expected_version": claimed["version"],
                        "result_text": "Stale worker",
                        "idempotency_key": str(uuid4()),
                    },
                )
                assert (
                    stale.isError
                    and "lease_invalid" in stale.content[0].text
                    and "recovery" in stale.content[0].text
                )
                message = await call(
                    "send_message",
                    {
                        "recipient_id": second["actor"]["id"],
                        "body": "Resume with the published handoff",
                        "idempotency_key": str(uuid4()),
                    },
                )
                assert rpc("messages/inbox", {})["items"][0]["ack_stage"] is None
                rpc(
                    "commands/execute",
                    {
                        "operation": "message.ack",
                        "resource_id": message["id"],
                        "arguments": {"stage": "processed"},
                        "idempotency_key": str(uuid4()),
                    },
                )
                assert (await call("outbox"))["items"][0]["recipient_ack_stage"] == "processed"

    try:
        asyncio.run(scenario())
    finally:
        bridge.close()
        spool.close()
