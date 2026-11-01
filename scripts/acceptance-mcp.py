"""MCP client used in an isolated container; no model or provider configuration."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "team_sync.mcp"], env=dict(os.environ)
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()

            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError("MCP acceptance command failed")
                return json.loads(result.content[0].text)

            async def command(operation, arguments, resource_id=None):
                return await call(
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

            if os.environ["ACCEPTANCE_PHASE"] == "seed":
                task = await command(
                    "task.create", {"title": "Cross-runtime artifact verification"}
                )
                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / "acceptance.bin"
                    source.write_bytes(b"Team Sync interoperability fixture\x00\xff")
                    artifact = await call(
                        "publish_artifact",
                        {"local_path": str(source), "idempotency_key": str(uuid4())},
                    )
                message = await command(
                    "message.send",
                    {
                        "recipient_id": os.environ["ACCEPTANCE_PARTNER"],
                        "body": f"Verify artifact:{artifact['id']} "
                        "and complete the shared work item.",
                    },
                )
                print(
                    json.dumps(
                        {
                            "task_id": task["id"],
                            "artifact_id": artifact["id"],
                            "message_id": message["id"],
                        }
                    )
                )
            else:
                tasks = (await call("list_items", {"collection": "tasks"}))["items"]
                task = next(item for item in tasks if item["id"] == os.environ["ACCEPTANCE_TASK"])
                if (
                    task["status"] != "done"
                    or task["assignee_id"] != os.environ["ACCEPTANCE_PARTNER"]
                ):
                    raise RuntimeError("Cross-runtime task outcome did not match")
                findings = (await call("list_items", {"collection": "findings"}))["items"]
                if not any(
                    item["author_id"] == os.environ["ACCEPTANCE_PARTNER"] for item in findings
                ):
                    raise RuntimeError("Cross-runtime finding was not visible over MCP")
                messages = (await call("inbox", {}))["items"]
                reply = next(
                    item
                    for item in messages
                    if item["sender_id"] == os.environ["ACCEPTANCE_PARTNER"]
                )
                await command("message.ack", {"stage": "processed"}, reply["id"])
                print(
                    json.dumps(
                        {
                            "shared_task": "passed",
                            "shared_findings": "passed",
                            "reply_received": "passed",
                        }
                    )
                )


if __name__ == "__main__":
    asyncio.run(main())
