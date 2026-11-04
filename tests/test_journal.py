"""Useful editing controls and a bounded, private, project-scoped audit journal."""

import json
from uuid import uuid4

from .conftest import enroll, execute, initialize
from .test_human_workflows import approved_review, command, get, upload


def update(client, project, collection, item, changes, *, token=None, force=False, reason=""):
    return execute(
        client,
        project,
        "resource.update",
        {
            "collection": collection,
            "expected_version": item["version"],
            "changes": changes,
            "force": force,
            "reason": reason,
        },
        item["id"],
        token,
    )


def test_metadata_fields_and_own_profile_permissions(client, project):
    agent = enroll(client, project)
    response = update(
        client,
        project,
        "actors",
        agent["actor"],
        {"notes": "Crypto specialist"},
        token=agent["token"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["notes"] == "Crypto specialist"
    assert (
        update(
            client, project, "actors", response.json(), {"role": "owner"}, token=agent["token"]
        ).status_code
        == 403
    )
    challenge = command(client, project, "challenge.create", {"title": "Challenge"})
    changed = update(
        client,
        project,
        "challenges",
        challenge,
        {"points": 500, "source_url": "https://ctf.example/challenge/1"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["points"] == 500
    assert (
        update(
            client, project, "challenges", changed.json(), {"source_url": "javascript:alert(1)"}
        ).status_code
        == 422
    )
    task = command(client, project, "task.create", {"title": "Work"})
    changed = update(
        client,
        project,
        "tasks",
        task,
        {"priority": "urgent", "due_at": "2026-11-05T12:00:00+02:00"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["priority"] == "urgent"
    context = get(client, project, "agent-context", agent["token"]).json()
    assert "log.create" in context["allowed_operations"]


def test_override_requires_lead_reason_and_fences_previous_worker(client, project):
    old, new = enroll(client, project, "Old"), enroll(client, project, "New")
    task = command(client, project, "task.create", {"title": "Work"})
    task = command(client, project, "task.claim", {"expected_version": 1}, task["id"], old["token"])
    changes = {"requested_assignee_id": new["actor"]["id"]}
    assert update(client, project, "tasks", task, changes).status_code == 409
    assert update(client, project, "tasks", task, changes, force=True).status_code == 422
    assert (
        update(
            client,
            project,
            "tasks",
            task,
            changes,
            force=True,
            reason="Handoff",
            token=old["token"],
        ).status_code
        == 403
    )
    changed = update(
        client, project, "tasks", task, changes, force=True, reason="Agent disconnected"
    )
    assert changed.status_code == 200, changed.text
    changed = changed.json()
    assert (
        changed["status"] == "open"
        and changed["assignee_id"] is None
        and changed["lease_until"] is None
    )
    assert changed["requested_assignee_id"] == new["actor"]["id"]
    assert (
        execute(
            client,
            project,
            "task.complete",
            {"expected_version": changed["version"], "result": "Late output"},
            task["id"],
            old["token"],
        ).status_code
        == 409
    )
    journal = get(client, project, "journal", resource_id=task["id"]).json()
    assert "task.override" in [row["type"] for row in journal["items"]]
    history = get(client, project, f"resources/tasks/{task['id']}/history").json()
    assert history["items"][0]["before"]["assignee_id"] == old["actor"]["id"]
    assert history["items"][0]["reason"] == "Agent disconnected"
    accepted = command(
        client,
        project,
        "task.claim",
        {"expected_version": changed["version"]},
        task["id"],
        new["token"],
    )
    assert accepted["assignee_id"] == new["actor"]["id"]


def test_review_override_revokes_old_approval_and_cannot_skip_report(client, project):
    agent, new = enroll(client, project, "Reviewer"), enroll(client, project, "Next")
    artifact = upload(client, project)
    task = approved_review(client, project, agent, artifact)
    assert (
        get(client, project, f"artifacts/{artifact['id']}/content", agent["token"]).status_code
        == 200
    )
    response = update(
        client,
        project,
        "tasks",
        task,
        {"status": "done", "result": "No report"},
        force=True,
        reason="Override",
    )
    assert response.status_code == 409
    response = update(
        client,
        project,
        "tasks",
        task,
        {"requested_assignee_id": new["actor"]["id"]},
        force=True,
        reason="Replace reviewer",
    )
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["approval_status"] == "pending" and changed["approved_by_id"] is None
    assert changed["analysis_started_at"] is None
    assert (
        get(client, project, f"artifacts/{artifact['id']}/content", agent["token"]).status_code
        == 403
    )
    assert (
        get(client, project, f"artifacts/{artifact['id']}/content", new["token"]).status_code == 403
    )


def test_journal_filters_pagination_budget_and_immutable_logs(client, project):
    agent = enroll(client, project, "Nova")
    task = command(client, project, "task.create", {"title": "Inspect evidence"})
    for level, body in [
        ("info", "Start"),
        ("warning", "Literal 50%_ retry"),
        ("error", "Failed"),
        ("debug", "x" * 20000),
    ]:
        command(
            client,
            project,
            "log.create",
            {"body": body, "level": level, "task_id": task["id"]},
            token=agent["token"],
        )
    response = get(client, project, "journal", collection="logs", limit=2)
    assert response.status_code == 200, response.text
    first = response.json()
    assert len(first["items"]) == 2 and first["has_more"]
    command(client, project, "log.create", {"body": "New concurrent log"})
    second = get(
        client, project, "journal", collection="logs", limit=2, before=first["next_before"]
    ).json()
    assert len(second["items"]) == 2 and not second["has_more"]
    assert min(row["seq"] for row in first["items"]) > max(row["seq"] for row in second["items"])
    warning = get(
        client, project, "journal", level="warning", actor_id=agent["actor"]["id"], query="50%_"
    ).json()
    assert len(warning["items"]) == 1
    assert warning["items"][0]["log"]["task_id"] == task["id"]
    assert not get(client, project, "journal", query="50%_nonexistent").json()["items"]
    bounded = get(client, project, "journal", collection="logs", max_chars=4000).json()
    assert len(json.dumps(bounded, ensure_ascii=False, indent=2)) <= 4000
    full_id = first["items"][0]["resource"]["id"]
    full = get(client, project, f"resources/logs/{full_id}").json()
    assert len(full["item"]["body"]) == 20000 and not full["editing"]["allowed"]
    assert update(client, project, "logs", full["item"], {"body": "Rewritten"}).status_code == 422
    outsider = initialize(client)
    assert get(client, outsider, "journal", actor_id=agent["actor"]["id"]).status_code == 404
    assert not get(client, outsider, "journal", resource_id=full_id).json()["items"]
    assert get(client, outsider, f"resources/logs/{full_id}").status_code == 404


def test_journal_never_exposes_private_message_text_or_credentials(client, project):
    sender, recipient = enroll(client, project, "Sender"), enroll(client, project, "Receiver")
    message = command(
        client,
        project,
        "message.send",
        {"recipient_id": recipient["actor"]["id"], "body": "PRIVATE-CONTENT-TEST"},
        token=sender["token"],
    )
    command(
        client, project, "message.ack", {"stage": "received"}, message["id"], recipient["token"]
    )
    journal = get(client, project, "journal").json()
    encoded = json.dumps(journal)
    assert "PRIVATE-CONTENT-TEST" not in encoded and sender["token"] not in encoded
    assert not any(row["type"] == "message.ack" for row in journal["items"])
    row = next(row for row in journal["items"] if row["type"] == "message.send")
    assert row["resource"]["readable"] is False
    own = get(
        client, project, "journal", recipient["token"], collection="messages", include_routine=True
    ).json()
    assert any(row["type"] == "message.ack" for row in own["items"])
    assert all(row["resource"]["readable"] for row in own["items"])
    assert not get(client, project, "journal", query="PRIVATE-CONTENT-TEST").json()["items"]


def test_log_replay_observer_denial_and_rate_limit(client, project):
    key = str(uuid4())
    first = command(client, project, "log.create", {"body": "Progress"}, key=key)
    replay = command(client, project, "log.create", {"body": "Progress"}, key=key)
    assert first == replay
    observer = enroll(client, project, "Observer", "observer")
    assert (
        execute(client, project, "log.create", {"body": "No"}, token=observer["token"]).status_code
        == 403
    )
    for _ in range(119):
        command(client, project, "log.create", {"body": "Progress"})
    response = execute(client, project, "log.create", {"body": "Over budget"})
    assert response.status_code == 429
    assert command(client, project, "log.create", {"body": "Progress"}, key=key) == first
