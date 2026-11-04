import json
from datetime import timedelta
from uuid import uuid4

import pytest

from team_sync import models as m
from team_sync.connection import load_connection
from team_sync.delivery import AgentSettings

from .conftest import enroll, execute, headers, initialize


def root(project):
    return f"/api/v1/projects/{project['project']['id']}"


def get(client, project, path, token=None, **params):
    return client.get(
        root(project) + "/" + path, headers=headers(token or project["token"]), params=params
    )


def test_personal_context_is_bounded_scoped_and_does_not_ack(client, project):
    worker, peer = enroll(client, project, "Worker"), enroll(client, project, "Peer")
    challenge = execute(client, project, "challenge.create", {"title": "Focus"}).json()
    task = execute(
        client, project, "task.create", {"title": "My work", "challenge_id": challenge["id"]}
    ).json()
    execute(client, project, "task.claim", {"expected_version": 1}, task["id"], worker["token"])
    execute(client, project, "task.create", {"title": "Available", "challenge_id": challenge["id"]})
    execute(client, project, "task.create", {"title": "Other challenge"})
    message = execute(
        client,
        project,
        "message.send",
        {"recipient_id": worker["actor"]["id"], "body": "For this worker"},
    ).json()
    execute(
        client,
        project,
        "message.send",
        {"recipient_id": peer["actor"]["id"], "body": "Private peer body"},
    )
    for i in range(6):
        execute(
            client,
            project,
            "finding.create",
            {
                "title": f"Evidence {i}",
                "body": "x" * 20000,
                "evidence": "y" * 20000,
                "challenge_id": challenge["id"],
                "task_id": task["id"],
            },
        )
    context = get(
        client,
        project,
        "agent-context",
        worker["token"],
        challenge_id=challenge["id"],
        max_chars=4000,
    ).json()
    assert len(json.dumps(context, ensure_ascii=False)) <= 4000
    assert context["sections"]["my_tasks"]["total"] == 1
    assert context["sections"]["available_tasks"]["total"] == 1
    assert context["sections"]["recent_findings"]["has_more"]
    assert context["sections"]["messages"]["total"] == 1
    assert context["truncated"]
    assert "Private peer body" not in json.dumps(context)
    assert worker["token"] not in json.dumps(context)
    assert "flag.verify" not in context["allowed_operations"]
    assert "task.release" in context["allowed_operations"]
    assert (
        get(client, project, "messages/" + message["id"], worker["token"]).json()["ack_stage"]
        is None
    )
    observer = enroll(client, project, role="observer")
    context = get(client, project, "agent-context", observer["token"]).json()
    assert context["allowed_operations"] == ["message.ack", "actor.rename", "activity.report"]
    assert all(
        not task["can_claim"] and task["available_actions"] == []
        for task in context["sections"]["available_tasks"]["items"]
    )


def test_context_large_rules_and_capabilities_cannot_defeat_budget(client):
    project = initialize(
        client,
        name="\x01" * 200,
        owner_name="\x02" * 200,
        rules="\x03" * 100000,
        capabilities={"flags": True, "k" * 10000: True},
    )
    context = get(client, project, "agent-context", max_chars=4000).json()
    assert len(json.dumps(context, ensure_ascii=False, indent=2)) <= 4000
    assert context["truncated"]
    assert "token_hash" not in json.dumps(context)
    assert context["project"]["rules_truncated"]
    assert (
        len(
            get(client, project, f"resources/projects/{project['project']['id']}").json()["item"][
                "rules"
            ]
        )
        == 100000
    )
    other = initialize(client)
    foreign = execute(client, other, "task.create", {"title": "Foreign"}).json()
    assert get(client, project, "agent-context", task_id=foreign["id"]).status_code == 404
    assert get(client, project, "agent-context", limit=21).status_code == 422


def test_resource_details_and_search_respect_private_messages_and_literal_query(client, project):
    peer = enroll(client, project)
    challenge = execute(client, project, "challenge.create", {"title": "Searchable"}).json()
    task = execute(
        client, project, "task.create", {"title": "need_% literal", "challenge_id": challenge["id"]}
    ).json()
    execute(client, project, "task.create", {"title": "needAX wildcard lookalike"})
    for i in range(3):
        execute(
            client,
            project,
            "finding.create",
            {
                "title": f"Needle {i}",
                "body": "Full body " * 1000,
                "evidence": "Full evidence",
                "task_id": task["id"],
                "challenge_id": challenge["id"],
            },
        )
    private = execute(
        client,
        project,
        "message.send",
        {"recipient_id": peer["actor"]["id"], "body": "Needle private"},
    ).json()
    detail = get(client, project, f"resources/tasks/{task['id']}", limit=2).json()
    assert detail["item"]["id"] == task["id"]
    assert detail["related"]["findings"]["total"] == 3
    assert detail["related"]["findings"]["has_more"]
    finding_id = detail["related"]["findings"]["items"][0]["id"]
    assert (
        len(get(client, project, f"resources/findings/{finding_id}").json()["item"]["body"]) > 1000
    )
    assert get(client, project, "resources/messages/" + private["id"]).status_code == 404
    assert (
        get(client, project, "resources/messages/" + private["id"], peer["token"]).json()["item"][
            "ack_stage"
        ]
        is None
    )
    result = get(client, project, "search", query="needle").json()
    assert result["total"] == 3
    assert all(i["collection"] == "findings" for i in result["items"])
    assert (
        get(client, project, "search", peer["token"], query="Needle", collection="messages").json()[
            "total"
        ]
        == 1
    )
    assert (
        get(client, project, "search", query="need_%", collection="tasks").json()["items"][0][
            "item"
        ]["id"]
        == task["id"]
    )
    assert get(client, project, "search", query="need_%").json()["total"] == 1
    assert (
        get(client, project, "search", query="needle", offset=2, limit=1).json()["has_more"]
        is False
    )
    actor = get(client, project, "resources/actors/" + peer["actor"]["id"]).json()["item"]
    assert "token_hash" not in actor and "token" not in actor


def test_release_preserves_handoff_atomically_and_fences_stale_worker(
    client, project, app, monkeypatch
):
    first, second = enroll(client, project), enroll(client, project)
    task = execute(client, project, "task.create", {"title": "Handoff"}).json()
    execute(client, project, "task.claim", {"expected_version": 1}, task["id"], first["token"])
    head = get(client, project, "events").json()["head"]
    action = {
        "expected_version": 2,
        "reason": "Parsed headers. Next: verify timezone; artifact is in the shared files.",
    }
    assert (
        execute(client, project, "task.release", action, task["id"], second["token"]).status_code
        == 409
    )
    original = app.state.core.emit

    def fail_after_handoff(db, proj, actor, event_type, payload):
        if event_type == "task.release":
            raise RuntimeError("Interrupted transaction")
        original(db, proj, actor, event_type, payload)

    with monkeypatch.context() as patch:
        patch.setattr(app.state.core, "emit", fail_after_handoff)
        with pytest.raises(RuntimeError):
            execute(client, project, "task.release", action, task["id"], first["token"])
    assert get(client, project, "events").json()["head"] == head
    assert get(client, project, "collections/findings").json()["items"] == []
    key = str(uuid4())
    released = execute(
        client, project, "task.release", action, task["id"], first["token"], key
    ).json()
    assert released["status"] == "open" and released["assignee_id"] is None
    assert released["version"] == 3
    assert (
        execute(client, project, "task.release", action, task["id"], first["token"], key).json()
        == released
    )
    assert get(client, project, "events").json()["head"] == head + 3
    assert (
        get(client, project, "collections/findings").json()["items"][0]["body"] == action["reason"]
    )
    assert (
        execute(
            client, project, "task.claim", {"expected_version": 3}, task["id"], second["token"]
        ).status_code
        == 200
    )
    assert (
        execute(
            client,
            project,
            "task.complete",
            {"expected_version": 4, "result": "Old worker"},
            task["id"],
            first["token"],
        ).status_code
        == 409
    )
    with app.state.core.context(project["project"]["id"], project["token"], write=True) as (
        db,
        _,
        __,
    ):
        item = db.get(m.Task, task["id"])
        item.lease_until = m.now() - timedelta(seconds=1)
    assert (
        execute(
            client,
            project,
            "task.release",
            {"expected_version": 4, "reason": "Too late"},
            task["id"],
            second["token"],
        ).status_code
        == 409
    )


def test_browser_session_is_project_scoped_csrf_protected_and_revocable(client, project):
    peer = enroll(client, project)
    base = root(project)
    response = client.post(base + "/browser-session", headers=headers(peer["token"]))
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert f"Path={base}/" in cookie and "Max-Age=604800" in cookie
    assert peer["token"] not in response.text
    assert client.get(base + "/snapshot").json()["actor"]["id"] == peer["actor"]["id"]
    action = {
        "operation": "task.create",
        "arguments": {"title": "Browser task"},
        "idempotency_key": str(uuid4()),
    }
    assert client.post(base + "/commands", json=action).status_code == 403
    origin = {"Origin": "http://testserver", "X-Team-Sync-Browser": "1"}
    assert (
        client.post(
            base + "/commands", headers={**origin, "Origin": "https://foreign.invalid"}, json=action
        ).status_code
        == 403
    )
    assert client.post(base + "/commands", headers=origin, json=action).status_code == 200
    assert client.get(base + "/snapshot", headers=headers("invalid")).status_code == 401
    other = initialize(client)
    assert client.get(root(other) + "/snapshot").status_code == 401
    assert (
        client.post(
            base + "/actors/" + peer["actor"]["id"] + "/revoke", headers=headers(project["token"])
        ).status_code
        == 200
    )
    assert client.get(base + "/snapshot").status_code == 401
    assert client.delete(base + "/browser-session", headers=origin).status_code == 200
    assert client.get(base + "/snapshot").status_code == 401
    response = client.post(
        "https://testserver" + base + "/browser-session", headers=headers(project["token"])
    )
    assert "Secure" in response.headers["set-cookie"]


def test_portable_connection_file_avoids_hybrid_credentials(tmp_path, monkeypatch):
    project_id = str(uuid4())
    access = {
        "schema_version": 1,
        "url": "https://team.example",
        "project_id": project_id,
        "token": "private-file-credential",
    }
    path = tmp_path / "access.json"
    path.write_text(json.dumps(access))
    monkeypatch.setenv("TEAM_SYNC_URL", "http://wrong.example")
    monkeypatch.setenv("TEAM_SYNC_TOKEN", "stale-environment-credential")
    assert load_connection(path)["url"] == access["url"]
    settings = AgentSettings.from_env(tmp_path / "cache.db", path)
    assert settings.project_id == project_id and settings.token == access["token"]
    monkeypatch.setenv("TEAM_SYNC_ACCESS_FILE", str(path))
    assert load_connection()["token"] == access["token"]
    assert access["token"] not in repr(settings) and access["token"] not in json.dumps(
        settings.binding()
    )
    path.write_text(json.dumps({**access, "schema_version": True}))
    with pytest.raises(ValueError):
        load_connection(path)


def test_two_browser_identities_keep_authorship_and_logout_separate(client, project):
    peer = enroll(client, project)
    base = root(project)
    for token in [project["token"], peer["token"]]:
        assert client.post(base + "/browser-session", headers=headers(token)).status_code == 200
    owner_headers = {"X-Team-Sync-Actor": project["actor"]["id"]}
    peer_headers = {"X-Team-Sync-Actor": peer["actor"]["id"]}
    assert client.get(base + "/snapshot", headers=owner_headers).json()["actor"]["role"] == "owner"
    assert client.get(base + "/snapshot", headers=peer_headers).json()["actor"]["role"] == "worker"
    assert (
        client.delete(
            base + "/browser-session",
            headers={**owner_headers, "Origin": "http://testserver", "X-Team-Sync-Browser": "1"},
        ).status_code
        == 200
    )
    assert client.get(base + "/snapshot", headers=owner_headers).status_code == 401
    assert client.get(base + "/snapshot", headers=peer_headers).status_code == 200
    assert client.get(base + "/snapshot").json()["actor"]["id"] == peer["actor"]["id"]


def test_outbox_tracks_only_authored_messages_and_recipient_receipts(client, project):
    peer, other = enroll(client, project), enroll(client, project)
    message = execute(
        client,
        project,
        "message.send",
        {"recipient_id": peer["actor"]["id"], "body": "Private assignment"},
    ).json()
    execute(
        client,
        project,
        "message.send",
        {"recipient_id": other["actor"]["id"], "body": "Private peer-to-peer"},
        token=peer["token"],
    )
    rows = get(client, project, "outbox").json()["items"]
    assert len(rows) == 1 and rows[0]["recipient_ack_stage"] is None
    assert get(client, project, "outbox", other["token"]).json()["items"] == []
    execute(client, project, "message.ack", {"stage": "received"}, message["id"], peer["token"])
    assert get(client, project, "outbox").json()["items"][0]["recipient_ack_stage"] == "received"
    execute(client, project, "message.ack", {"stage": "processed"}, message["id"], peer["token"])
    assert get(client, project, "outbox").json()["items"][0]["recipient_ack_stage"] == "processed"
    assert (
        get(client, project, "agent-context").json()["sections"]["sent_messages"]["items"][0][
            "recipient_ack_stage"
        ]
        == "processed"
    )
    assert "Private peer-to-peer" not in json.dumps(get(client, project, "outbox").json())
