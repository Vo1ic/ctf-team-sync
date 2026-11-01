import asyncio
import json
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from threading import Thread
from uuid import uuid4

import httpx
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .conftest import enroll, execute, headers


@pytest.fixture
def live_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("HTTP server did not start")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


def test_sse_reconnect_and_live_long_poll(client, project, live_server):
    root = f"{live_server}/api/v1/projects/{project['project']['id']}/events"
    auth = headers(project["token"])
    initial_head = client.get(root.replace(live_server, ""), headers=auth).json()["head"]
    execute(client, project, "task.create", {"title": "Replay me"})
    with httpx.Client(timeout=5) as remote:
        with remote.stream(
            "GET",
            root + "/stream",
            headers={
                **auth,
                "Last-Event-ID": str(initial_head),
            },
        ) as response:
            assert response.status_code == 200
            lines = response.iter_lines()
            assert next(lines) == f"id: {initial_head + 1}"
            assert next(lines) == "event: change"
            event = json.loads(next(lines).removeprefix("data: "))
            assert event["type"] == "task.create"

    async def scenario():
        async with httpx.AsyncClient(timeout=5) as remote:
            waiter = asyncio.create_task(
                remote.get(
                    root + "/wait",
                    params={"after": initial_head + 1, "timeout": 3},
                    headers=auth,
                )
            )
            await asyncio.sleep(0.1)
            await remote.post(
                root.removesuffix("/events") + "/commands",
                headers=auth,
                json={
                    "operation": "message.send",
                    "arguments": {"body": "Wake the waiter"},
                    "idempotency_key": str(uuid4()),
                },
            )
            batch = (await waiter).json()
            assert batch["items"][0]["type"] == "message.send"

    asyncio.run(scenario())


def test_two_stdio_mcp_clients_share_remote_state(client, project, live_server, tmp_path):
    first, second = enroll(client, project, "MCP A"), enroll(client, project, "MCP B")

    @asynccontextmanager
    async def agent_session(agent):
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "team_sync.mcp"],
            env={
                **os.environ,
                "TEAM_SYNC_URL": live_server,
                "TEAM_SYNC_PROJECT_ID": project["project"]["id"],
                "TEAM_SYNC_TOKEN": agent["token"],
            },
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield session

    async def call(session, name, arguments=None):
        response = await session.call_tool(name, arguments or {})
        assert not response.isError, response.content
        return json.loads(response.content[0].text)

    async def action(session, operation, arguments, resource_id=None):
        return await call(
            session,
            "command",
            {
                "action": {
                    "operation": operation,
                    "arguments": arguments,
                    "resource_id": resource_id,
                    "idempotency_key": str(uuid4()),
                }
            },
        )

    async def scenario():
        async with agent_session(first) as a, agent_session(second) as b:
            tools = {tool.name for tool in (await a.list_tools()).tools}
            assert {"bootstrap", "command", "wait_events", "publish_artifact"} <= tools
            assert (await call(a, "bootstrap"))["actor"]["id"] == first["actor"]["id"]
            contracts = await call(a, "command_contracts")
            assert "expected_version" in contracts["task.claim"]["properties"]
            task = await action(a, "task.create", {"title": "Cross-client work"})
            claimed = await action(b, "task.claim", {"expected_version": 1}, task["id"])
            assert claimed["assignee_id"] == second["actor"]["id"]
            await action(
                a,
                "message.send",
                {
                    "recipient_id": second["actor"]["id"],
                    "body": "Here is the shared artifact",
                },
            )
            inbox = await call(b, "inbox")
            message = inbox["items"][0]
            assert message["ack_stage"] is None
            await action(b, "message.ack", {"stage": "processed"}, message["id"])
            events = await call(b, "wait_events", {"after": 0, "timeout": 0})
            assert any(item["type"] == "message.send" for item in events["items"])
            source, destination = tmp_path / "source.bin", tmp_path / "download.bin"
            source.write_bytes(b"shared binary\x00\xff")
            artifact = await call(
                a,
                "publish_artifact",
                {
                    "local_path": str(source),
                    "idempotency_key": str(uuid4()),
                },
            )
            await call(
                b,
                "fetch_artifact",
                {
                    "artifact_id": artifact["id"],
                    "local_path": str(destination),
                },
            )
            assert source.read_bytes() == destination.read_bytes()
            # Existing local files must survive even if another fetch is requested.
            response = await b.call_tool(
                "fetch_artifact",
                {
                    "artifact_id": artifact["id"],
                    "local_path": str(destination),
                },
            )
            assert response.isError
            assert destination.read_bytes() == source.read_bytes()

    asyncio.run(scenario())
