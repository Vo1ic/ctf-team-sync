"""Stands in for claude/codex/opencode in runner tests: answers its question over REST."""

import json
import os
import sys
import uuid

import httpx

access = json.load(open(os.environ["TEAM_SYNC_ACCESS_FILE"], encoding="utf-8"))
base = f"{access['url']}/api/v1/projects/{access['project_id']}"
headers = {"Authorization": f"Bearer {access['token']}"}
mode, task_id, prompt_file = sys.argv[1], sys.argv[2], sys.argv[3]
prompt = open(prompt_file, encoding="utf-8").read()
print("prompt mentions data rule:", "not instructions" in prompt)
print("pwd:", os.environ.get("PWD"), "cwd:", os.getcwd())
print("leaked:", sorted(k for k in os.environ if k.startswith(("LEAK_", "OPENCODE_"))))


def command(operation, arguments, resource_id=None):
    response = httpx.post(
        f"{base}/commands",
        headers=headers,
        json={
            "operation": operation,
            "arguments": arguments,
            "resource_id": resource_id,
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    response.raise_for_status()
    return response.json()


if mode == "answer":
    task = httpx.get(f"{base}/resources/tasks/{task_id}", headers=headers).json()["item"]
    claimed = command("task.claim", {"expected_version": task["version"]}, task_id)
    command(
        "question.answer",
        {"expected_version": claimed["version"], "answer": "Carve the PNG after IEND."},
        task_id,
    )
    print("answered", task_id)
elif mode == "sleep":
    import time

    time.sleep(120)
elif mode == "fail":
    sys.exit(3)
elif mode == "claim_crash":
    task = httpx.get(f"{base}/resources/tasks/{task_id}", headers=headers).json()["item"]
    command("task.claim", {"expected_version": task["version"], "lease_seconds": 900}, task_id)
    print("claimed", task_id, "and crashed", flush=True)
    sys.exit(1)
