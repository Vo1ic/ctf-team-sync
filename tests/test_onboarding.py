"""Invites, readable agent names, CTF.md, cooperative tasks and agent-facing ergonomics."""

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import select

from team_sync import models as m

from .conftest import enroll, execute, headers

NAME = re.compile(r"[A-Za-z0-9]+-[A-Za-z]+_[a-z0-9]{4}")


def root(project):
    return f"/api/v1/projects/{project['project']['id']}"


def invite(client, project, **body):
    response = client.post(root(project) + "/invites", headers=headers(project["token"]), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def join(client, token, **body):
    return client.post("/api/v1/join", headers=headers(token), json=body)


def test_invite_creates_teammate_and_owner_named_agent(client, project, app):
    created = invite(client, project, label="Lviv team", max_uses=2)
    assert created["token"].startswith("inv_")
    assert "token_hash" not in created["invite"]
    joined = join(client, created["token"], human_name="Vo1ic", capabilities=["forensics"])
    assert joined.status_code == 201, joined.text
    data = joined.json()
    human, agent = data["human"]["actor"], data["agent"]["actor"]
    assert human["kind"] == "human" and human["role"] == "worker"
    assert agent["owner_id"] == human["id"] and agent["role"] == "worker"
    assert re.fullmatch(r"Vo1ic-[A-Za-z]+_[a-z0-9]{4}", agent["name"])
    assert "підказки" in data["briefing"] and "CTF.md" in data["briefing"]
    # The human credential works and can add more agents for itself only.
    human_token = data["human"]["token"]
    own = client.post(
        root(project) + "/actors", headers=headers(human_token), json={"kind": "agent"}
    )
    assert own.status_code == 201 and own.json()["actor"]["owner_id"] == human["id"]
    assert own.json()["actor"]["name"].startswith("Vo1ic-")
    assert (
        client.post(
            root(project) + "/actors", headers=headers(human_token), json={"kind": "human"}
        ).status_code
        == 403
    )
    # An agent can enroll a sibling for the same human (one identity per process).
    sibling = client.post(
        root(project) + "/actors",
        headers=headers(data["agent"]["token"]),
        json={"kind": "agent", "name": "Scout"},
    )
    assert sibling.status_code == 201
    assert sibling.json()["actor"]["name"] == "Vo1ic-Scout"
    assert sibling.json()["actor"]["owner_id"] == human["id"]
    # Uses are limited, invalid and expired invites are rejected.
    assert join(client, created["token"], human_name="Second").status_code == 201
    assert join(client, created["token"], human_name="Third").status_code == 409
    assert join(client, "inv_nope", human_name="X").status_code == 401
    other = invite(client, project)
    with app.state.core.sessions.begin() as db:
        row = db.scalar(select(m.Invite).where(m.Invite.id == other["invite"]["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert join(client, other["token"], human_name="Late").status_code == 401
    worker = enroll(client, project, "Plain worker")
    assert (
        client.post(
            root(project) + "/invites", headers=headers(worker["token"]), json={}
        ).status_code
        == 403
    )
    listed = client.get(root(project) + "/invites", headers=headers(project["token"])).json()
    assert {item["uses"] for item in listed["items"]} == {2, 0}


def test_ctf_md_is_editable_but_mandatory_rules_stay(client, project):
    me = client.get(root(project) + "/me", headers=headers(project["token"])).json()
    assert me["service"] == "ctf-team-sync" and me["actor"]["name"] == "Captain"
    text = client.get(root(project) + "/ctf.md", headers=headers(project["token"])).text
    assert text.startswith("# CTF.md — Test CTF") and "Як працюємо" in text
    updated = execute(
        client,
        project,
        "resource.update",
        {
            "collection": "projects",
            "expected_version": 1,
            "changes": {"briefing": "CTF Lviv 2026: jeopardy, 48h.", "rules": "Scope: ctfd"},
        },
        project["project"]["id"],
    )
    assert updated.status_code == 200, updated.text
    briefing = client.get(root(project) + "/briefing", headers=headers(project["token"])).json()
    assert "CTF Lviv 2026" in briefing["text"] and "Scope: ctfd" in briefing["text"]
    assert "Як працюємо" not in briefing["text"]
    assert "Не відкривай, не купуй і не розблоковуй підказки" in briefing["text"]
    assert client.get("/health").json()["service"] == "ctf-team-sync"


def test_cooperation_join_without_taking_the_lease(client, project):
    human = join(
        client, invite(client, project)["token"], human_name="Olena", create_agent=True
    ).json()
    agent_token = human["agent"]["token"]
    worker = enroll(client, project, "Runner")
    forensics = execute(
        client, project, "challenge.create", {"title": "Disk", "category": "Forensics"}
    ).json()
    memory = execute(
        client, project, "challenge.create", {"title": "Mem", "category": "forensics-memory"}
    ).json()
    web = execute(client, project, "challenge.create", {"title": "Site", "category": "web"}).json()
    tasks = [
        execute(client, project, "task.create", {"title": title, "challenge_id": cid}).json()
        for title, cid in [("Image", forensics["id"]), ("Dump", memory["id"]), ("XSS", web["id"])]
    ]
    claimed = execute(
        client, project, "task.claim", {"expected_version": 1}, tasks[0]["id"], worker["token"]
    )
    assert claimed.status_code == 200
    # Someone else's live task: claiming fails, joining works and keeps the lease.
    assert (
        execute(
            client, project, "task.claim", {"expected_version": 2}, tasks[0]["id"], agent_token
        ).status_code
        == 409
    )
    joined = execute(
        client, project, "task.join", {"note": "second pair of eyes"}, tasks[0]["id"], agent_token
    )
    assert joined.status_code == 200 and joined.json()["assignee_id"] == worker["actor"]["id"]
    detail = client.get(
        root(project) + f"/resources/tasks/{tasks[0]['id']}", headers=headers(agent_token)
    ).json()
    assert [p["name"] for p in detail["item"]["participants"]] == [human["agent"]["actor"]["name"]]
    # "I take all forensics" for the human, done by their agent.
    bulk = execute(
        client,
        project,
        "task.join_many",
        {"category": "forensics", "actor_id": human["human"]["actor"]["id"]},
        token=agent_token,
    )
    assert bulk.status_code == 200, bulk.text
    assert set(bulk.json()["joined"]) == {tasks[0]["id"], tasks[1]["id"]}
    context = client.get(
        root(project) + "/agent-context", headers=headers(agent_token), params={"limit": 10}
    ).json()
    joined_ids = {item["id"] for item in context["sections"]["joined_tasks"]["items"]}
    assert joined_ids == {tasks[0]["id"], tasks[1]["id"]}
    assert context["self"]["owner_name"] == "Olena"
    # Strangers cannot sign other people up.
    assert (
        execute(
            client,
            project,
            "task.join",
            {"actor_id": human["human"]["actor"]["id"]},
            tasks[2]["id"],
            worker["token"],
        ).status_code
        == 403
    )
    left = execute(client, project, "task.leave", {}, tasks[0]["id"], agent_token)
    assert left.status_code == 200
    journal = client.get(
        root(project) + "/journal", headers=headers(project["token"]), params={"query": "Image"}
    ).json()
    assert {"task.join", "task.leave"} <= {item["type"] for item in journal["items"]}


def test_flags_link_to_tasks_and_quick_verification(client, project):
    challenge = execute(client, project, "challenge.create", {"title": "Crypto 1"}).json()
    task = execute(
        client, project, "task.create", {"title": "Solve", "challenge_id": challenge["id"]}
    ).json()
    flag = execute(client, project, "flag.create", {"value": "CTF{x}", "task_id": task["id"]})
    assert flag.status_code == 200, flag.text
    assert flag.json()["challenge_id"] == challenge["id"]
    duplicate = execute(client, project, "flag.create", {"value": "CTF{x}", "task_id": task["id"]})
    assert duplicate.status_code == 409 and duplicate.json()["existing_id"] == flag.json()["id"]
    verified = execute(
        client,
        project,
        "flag.verify",
        {"expected_version": 1, "status": "confirmed"},
        flag.json()["id"],
    )
    assert verified.status_code == 200 and verified.json()["verification_status"] == "confirmed"
    assert "Captain" in verified.json()["verification"]["evidence"]
    stale = execute(
        client,
        project,
        "flag.verify",
        {"expected_version": 1, "status": "rejected"},
        flag.json()["id"],
    )
    assert stale.status_code == 409 and stale.json()["current_version"] == 2
    detail = client.get(
        root(project) + f"/resources/tasks/{task['id']}", headers=headers(project["token"])
    ).json()
    assert detail["related"]["flags"]["total"] == 1


def test_duplicates_are_reported_with_existing_ids(client, project):
    first = execute(client, project, "challenge.create", {"title": "Radio Lviv"}).json()
    again = execute(client, project, "challenge.create", {"title": " radio lviv "})
    assert again.status_code == 409 and again.json()["existing_id"] == first["id"]
    assert (
        execute(
            client, project, "challenge.create", {"title": "Radio Lviv", "allow_duplicate": True}
        ).status_code
        == 200
    )
    task = execute(
        client, project, "task.create", {"title": "Decode", "challenge_id": first["id"]}
    ).json()
    repeated = execute(
        client,
        project,
        "task.create",
        {"title": "Decode", "challenge_id": first["id"], "category": "misc", "points": 100},
    )
    assert repeated.status_code == 409 and repeated.json()["existing_id"] == task["id"]


def test_context_never_hides_items_and_search_matches_words(client, project):
    challenge = execute(
        client,
        project,
        "challenge.create",
        {
            "title": "Clipboard Heist Pt. 2",
            "description": "Download https://ctf.example/files/a.zip?token=SECRETSIGNED&x=1",
        },
    ).json()
    task = execute(
        client, project, "task.create", {"title": "Recover", "challenge_id": challenge["id"]}
    ).json()
    for i in range(8):
        execute(
            client,
            project,
            "finding.create",
            {
                "title": f"Icon cache {i}",
                "body": "b" * 5000,
                "evidence": "e" * 5000,
                "task_id": task["id"],
            },
        )
        execute(client, project, "task.create", {"title": f"Other {i}", "description": "d" * 3000})
    context = client.get(
        root(project) + "/agent-context",
        headers=headers(project["token"]),
        params={"limit": 10, "max_chars": 4000},
    ).json()
    assert len(json.dumps(context, ensure_ascii=False, indent=2)) <= 4000 or context["truncated"]
    for name, section in context["sections"].items():
        if section["total"]:
            assert section["items"], name
        if section["total"] > len(section["items"]):
            assert section["omitted"] == section["total"] - len(section["items"])
            assert section["more"]
    assert "SECRETSIGNED" not in json.dumps(context)
    found = client.get(
        root(project) + "/search",
        headers=headers(project["token"]),
        params={"query": "clipboard heist 2 icon"},
    ).json()
    assert found["total"] == 8 and {i["collection"] for i in found["items"]} == {"findings"}
    assert "SECRETSIGNED" not in json.dumps(found)
    full = client.get(
        root(project) + f"/resources/challenges/{challenge['id']}",
        headers=headers(project["token"]),
    ).json()
    assert "SECRETSIGNED" in full["item"]["description"]


def test_mcp_identity_per_process_invite_join_and_hot_reload(
    client, project, live_server, tmp_path
):
    created = invite(client, project)
    invite_file = tmp_path / "invite.json"
    invite_file.write_text(json.dumps({"url": live_server, "invite": created["token"]}))
    state = tmp_path / "state"

    def parameters(path):
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "team_sync.mcp"],
            env={
                **os.environ,
                "TEAM_SYNC_ACCESS_FILE": str(path),
                "TEAM_SYNC_STATE_DIR": str(state),
            },
        )

    async def call(session, name, args=None, error=False):
        response = await session.call_tool(name, args or {})
        assert response.isError == error, response.content
        text = response.content[0].text
        return json.loads(text[text.index("{") :]) if error else json.loads(text)

    async def scenario():
        async with stdio_client(parameters(invite_file)) as (reader, writer):
            async with ClientSession(reader, writer) as first:
                await first.initialize()
                # Contracts work without any server identity.
                assert "examples" in await call(
                    first, "command_contracts", {"operation": "flag.create"}
                )
                pending = await call(first, "get_context", error=True)
                assert pending["code"] == "join_required"
                joined = await call(first, "join_team", {"human_name": "Vo1ic"})
                assert NAME.fullmatch(joined["you"]["name"]) and joined["you"]["kind"] == "agent"
                assert joined["you"]["owner_name"] == "Vo1ic"
                assert "підказки" in joined["ctf_md"]
                me = await call(first, "whoami")
                assert me["terminal_title"].startswith(joined["you"]["name"])
                async with stdio_client(parameters(invite_file)) as (r2, w2):
                    async with ClientSession(r2, w2) as second:
                        await second.initialize()
                        other = await call(second, "whoami")
                        # A second live process never shares the first one's identity.
                        assert other["actor_id"] != me["actor_id"]
                        assert other["name"].startswith("Vo1ic-")
                        assert other["identity_source"] == "session-agent"
        # Hot reload: a wrong URL is reported with the URL, fixed without a restart.
        access = tmp_path / "agent.json"
        agent = enroll(client, project, "Fixed")
        access.write_text(
            json.dumps(
                {
                    "url": "http://127.0.0.1:9",
                    "project_id": project["project"]["id"],
                    "token": agent["token"],
                }
            )
        )
        async with stdio_client(parameters(access)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                problem = await call(session, "whoami", error=True)
                assert problem["code"] == "server_unreachable"
                assert "127.0.0.1:9" in problem["detail"]
                access.write_text(
                    json.dumps(
                        {
                            "url": live_server,
                            "project_id": project["project"]["id"],
                            "token": agent["token"],
                        }
                    )
                )
                fixed = await call(session, "whoami")
                assert fixed["actor_id"] == agent["actor"]["id"]
                assert fixed["identity_source"] == "direct"

    asyncio.run(scenario())


def test_pooled_identity_is_reused_and_revoked_one_is_retired(
    client, project, live_server, tmp_path
):
    import httpx

    from team_sync import identity

    seed = tmp_path / "owner.json"
    seed.write_text(
        json.dumps(
            {"url": live_server, "project_id": project["project"]["id"], "token": project["token"]}
        )
    )
    with httpx.Client() as http:
        locks = []
        first = identity.resolve(http, seed, locks=locks)
        assert first.actor["kind"] == "agent" and first.actor["name"].startswith("Captain-")
        for lock in locks:
            lock.release()
        locks = []
        again = identity.resolve(http, seed, locks=locks)
        assert again.actor["id"] == first.actor["id"]  # a free pooled identity is reused
        for lock in locks:
            lock.release()
        revoked = client.post(
            f"{root(project)}/actors/{first.actor['id']}/revoke", headers=headers(project["token"])
        )
        assert revoked.status_code == 200
        fresh = identity.resolve(http, seed, locks=[])
        assert fresh.actor["id"] != first.actor["id"]
        assert list(Path(first.agent_file).parent.glob("*.revoked"))
        direct = identity.resolve(http, seed, mode="direct", locks=[])
        assert direct.actor["name"] == "Captain"
