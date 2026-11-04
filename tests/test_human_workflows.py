"""Human decisions, independent agents, durable reports and overload boundaries."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from team_sync import models as m

from .conftest import enroll, execute, headers


def get(client, project, path, token=None, **params):
    return client.get(
        f"/api/v1/projects/{project['project']['id']}/{path}",
        headers=headers(token or project["token"]),
        params=params,
    )


def upload(client, project, name="evidence.txt", content=b"Synthetic CTF evidence"):
    response = client.post(
        f"/api/v1/projects/{project['project']['id']}/artifacts",
        headers={**headers(project["token"]), "Idempotency-Key": str(uuid4())},
        params={"name": name, "requires_review": True, "media_type": "text/plain"},
        content=content,
    )
    assert response.status_code == 201, response.text
    return response.json()


def command(client, project, operation, args=None, ident=None, token=None, key=None):
    response = execute(client, project, operation, args, ident, token, key)
    assert response.status_code == 200, response.text
    return response.json()


def approved_review(client, project, agent, artifact):
    task = command(
        client, project, "artifact.review.request", ident=artifact["id"], token=agent["token"]
    )
    task = command(
        client,
        project,
        "artifact.review.decide",
        {
            "expected_version": task["version"],
            "decision": "approved",
        },
        task["id"],
    )
    return command(
        client,
        project,
        "artifact.review.start",
        {
            "expected_version": task["version"],
        },
        task["id"],
        agent["token"],
    )


def test_protected_bytes_require_named_claim_human_decision_and_explicit_start(client, project):
    agent, peer = enroll(client, project, "Nova"), enroll(client, project, "Atlas")
    # An agent with administrative privileges still cannot impersonate a human decision.
    powerful = enroll(client, project, "Agent owner", role="owner")
    artifact = upload(client, project)
    path = f"artifacts/{artifact['id']}/content"
    assert get(client, project, path, agent["token"]).status_code == 403
    allowed = get(client, project, "agent-context", agent["token"]).json()["allowed_operations"]
    assert "artifact.review.decide" not in allowed and "artifact.archive" not in allowed
    assert get(client, project, path).content == b"Synthetic CTF evidence"
    task = command(
        client, project, "artifact.review.request", ident=artifact["id"], token=agent["token"]
    )
    assert task["assignee_id"] == agent["actor"]["id"]
    assert task["approval_status"] == "pending"
    assert get(client, project, path, agent["token"]).status_code == 403
    decision = {"expected_version": task["version"], "decision": "approved"}
    assert (
        execute(
            client, project, "artifact.review.decide", decision, task["id"], powerful["token"]
        ).status_code
        == 403
    )
    assert (
        execute(
            client, project, "artifact.review.request", {}, artifact["id"], peer["token"]
        ).status_code
        == 409
    )
    task = command(client, project, "artifact.review.decide", decision, task["id"])
    assert get(client, project, path, agent["token"]).status_code == 403
    task = command(
        client,
        project,
        "artifact.review.start",
        {
            "expected_version": task["version"],
        },
        task["id"],
        agent["token"],
    )
    assert get(client, project, path, agent["token"]).status_code == 200
    assert get(client, project, path, peer["token"]).status_code == 403
    assert (
        execute(
            client,
            project,
            "task.complete",
            {
                "expected_version": task["version"],
                "result": "skip README",
            },
            task["id"],
            agent["token"],
        ).status_code
        == 409
    )
    done = command(
        client,
        project,
        "artifact.review.complete",
        {
            "expected_version": task["version"],
            "readme": "# README\nSynthetic result.",
        },
        task["id"],
        agent["token"],
    )
    assert done["status"] == "done"
    resource = get(client, project, f"resources/artifacts/{artifact['id']}").json()
    report = resource["related"]["findings"]["items"][0]
    assert report["author_id"] == agent["actor"]["id"] and report["task_id"] == task["id"]
    assert "README" in report["body"]
    assert get(client, project, path, agent["token"]).status_code == 403


def test_unusable_report_archives_recoverably_and_reassignment_needs_new_permission(
    client, project
):
    agent, peer = enroll(client, project), enroll(client, project)
    artifact = upload(client, project, content=b"Empty lead, no useful evidence")
    task = approved_review(client, project, agent, artifact)
    released = command(
        client,
        project,
        "task.release",
        {
            "expected_version": task["version"],
            "reason": "Hand over to another specialist",
        },
        task["id"],
        agent["token"],
    )
    claimed = command(
        client,
        project,
        "task.claim",
        {
            "expected_version": released["version"],
        },
        task["id"],
        peer["token"],
    )
    assert claimed["approval_status"] == "pending" and claimed["analysis_started_at"] is None
    assert (
        get(client, project, f"artifacts/{artifact['id']}/content", peer["token"]).status_code
        == 403
    )
    decided = command(
        client,
        project,
        "artifact.review.decide",
        {
            "expected_version": claimed["version"],
            "decision": "approved",
        },
        task["id"],
    )
    started = command(
        client,
        project,
        "artifact.review.start",
        {
            "expected_version": decided["version"],
        },
        task["id"],
        peer["token"],
    )
    finish = {
        "expected_version": started["version"],
        "readme": "# README\nNo lead.",
        "verdict": "unusable",
    }
    assert (
        execute(
            client, project, "artifact.review.complete", finish, task["id"], peer["token"]
        ).status_code
        == 422
    )
    command(
        client,
        project,
        "artifact.review.complete",
        {**finish, "reason": "No CTF data"},
        task["id"],
        peer["token"],
    )
    stored = get(client, project, f"resources/artifacts/{artifact['id']}").json()["item"]
    assert stored["archived"] and stored["archive_reason"] == "No CTF data"
    path = f"artifacts/{artifact['id']}/content"
    assert get(client, project, path, peer["token"]).status_code == 410
    assert get(client, project, path).content == b"Empty lead, no useful evidence"
    restored = command(
        client,
        project,
        "artifact.archive",
        {
            "expected_version": stored["version"],
            "archived": False,
            "reason": "Second opinion",
        },
        artifact["id"],
    )
    assert not restored["archived"]


def test_human_archive_cancels_review_and_decline_never_unlocks_bytes(client, project):
    agent = enroll(client, project)
    artifact = upload(client, project)
    task = command(
        client, project, "artifact.review.request", ident=artifact["id"], token=agent["token"]
    )
    command(
        client,
        project,
        "artifact.archive",
        {"expected_version": 1, "archived": True, "reason": "Wrong file"},
        artifact["id"],
    )
    current = get(client, project, f"resources/tasks/{task['id']}").json()["item"]
    assert current["status"] == "done" and current["approval_status"] == "declined"
    assert (
        execute(
            client,
            project,
            "artifact.review.decide",
            {
                "expected_version": task["version"],
                "decision": "approved",
            },
            task["id"],
        ).status_code
        == 409
    )


def test_agent_can_delegate_to_human_without_faking_acceptance(client, project):
    agent, peer = enroll(client, project), enroll(client, project)
    task = command(
        client,
        project,
        "task.create",
        {"title": "Check the CTF rules", "requested_assignee_id": project["actor"]["id"]},
        token=agent["token"],
    )
    assert task["status"] == "open" and task["assignee_id"] is None
    notice = get(client, project, "attention").json()["items"][0]
    assert notice["task_id"] == task["id"] and notice["kind"] == "assignment"
    assert (
        execute(
            client, project, "task.claim", {"expected_version": 1}, task["id"], peer["token"]
        ).status_code
        == 403
    )
    own = get(client, project, "agent-context", peer["token"]).json()
    assert own["sections"]["available_tasks"]["total"] == 0
    claimed = command(client, project, "task.claim", {"expected_version": 1}, task["id"])
    command(
        client,
        project,
        "task.complete",
        {"expected_version": claimed["version"], "result": "Confirmed allowed scope"},
        task["id"],
    )


def test_names_are_unique_and_activity_is_explicit_and_expires(client, project, app):
    path = f"/api/v1/projects/{project['project']['id']}/actors"
    agents = [
        client.post(path, headers=headers(project["token"]), json={}).json() for _ in range(2)
    ]
    assert [item["actor"]["name"] for item in agents] == ["Nova", "Atlas"]
    renamed = command(
        client,
        project,
        "actor.rename",
        {"expected_version": 1, "name": "Nova"},
        agents[1]["actor"]["id"],
        agents[1]["token"],
    )
    assert renamed["name"] == "Nova 2"
    activity = command(
        client,
        project,
        "activity.report",
        {"state": "blocked", "detail": "Need human decision", "ttl_seconds": 30},
        token=agents[0]["token"],
    )
    actor_path = f"resources/actors/{agents[0]['actor']['id']}"
    assert not get(client, project, actor_path).json()["item"]["activity"]["stale"]
    with app.state.core.sessions.begin() as db:
        item = db.scalar(select(m.Activity).where(m.Activity.id == activity["id"]))
        item.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    stored = get(client, project, actor_path).json()["item"]
    assert stored["active"] and stored["activity"]["stale"]


def test_attention_dedup_status_expiry_privacy_and_fair_priority(client, project, app):
    agent, peer = enroll(client, project), enroll(client, project)
    params = {"recipient_id": agent["actor"]["id"], "body": "Same evidence"}
    first = command(client, project, "message.send", params, key="first")
    repeated = command(client, project, "message.send", params, key="second")
    assert repeated["id"] == first["id"] and repeated["duplicate_count"] == 2
    assert command(client, project, "message.send", params, key="second") == repeated
    for i in range(3):
        command(
            client,
            project,
            "message.send",
            {
                "recipient_id": agent["actor"]["id"],
                "body": f"Progress {i}",
                "kind": "status",
                "group_key": "scan",
            },
        )
    for i in range(4):
        command(
            client,
            project,
            "message.send",
            {"recipient_id": agent["actor"]["id"], "body": f"Urgent {i}", "priority": "urgent"},
        )
    command(
        client,
        project,
        "message.send",
        {"recipient_id": agent["actor"]["id"], "body": "Peer needs help", "priority": "urgent"},
        token=peer["token"],
    )
    private = command(
        client, project, "message.send", {"recipient_id": peer["actor"]["id"], "body": "Private"}
    )
    expired = command(client, project, "message.send", {**params, "body": "Old notice"})
    with app.state.core.sessions.begin() as db:
        db.get(m.Message, expired["id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    attention = get(client, project, "attention", agent["token"], limit=4).json()
    assert attention["total"] == 7 and attention["has_more"]
    assert attention["items"][1]["sender_id"] == peer["actor"]["id"]
    assert attention["items"][3]["priority"] == "normal"
    all_items = get(client, project, "attention", agent["token"]).json()["items"]
    assert private["id"] not in {item["id"] for item in all_items}
    latest = next(item for item in all_items if item["kind"] == "status")
    assert latest["body"] == "Progress 2"
    command(client, project, "message.ack", {"stage": "processed"}, latest["id"], agent["token"])
    assert not any(
        item["kind"] == "status"
        for item in get(client, project, "attention", agent["token"]).json()["items"]
    )
    assert len(get(client, project, "inbox", agent["token"]).json()["items"]) == 10


def test_urgent_budget_allows_receipts_and_human_decisions(client, project):
    agent = enroll(client, project)
    for i in range(20):
        command(client, project, "message.send", {"body": f"Urgent {i}", "priority": "urgent"})
    blocked = execute(client, project, "message.send", {"body": "Too many", "priority": "urgent"})
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "60"
    artifact = upload(client, project)
    task = command(
        client, project, "artifact.review.request", ident=artifact["id"], token=agent["token"]
    )
    command(
        client,
        project,
        "artifact.review.decide",
        {"expected_version": task["version"], "decision": "declined"},
        task["id"],
    )
    assert (
        get(client, project, f"artifacts/{artifact['id']}/content", agent["token"]).status_code
        == 403
    )


def test_total_send_budget_counts_duplicates_but_not_idempotent_replay_or_receipts(client, project):
    agent = enroll(client, project)
    args = {"body": "Identical flood", "recipient_id": agent["actor"]["id"]}
    first = command(client, project, "message.send", args, key="first")
    for index in range(119):
        last = command(client, project, "message.send", args, key=f"repeat-{index}")
    assert last["id"] == first["id"] and last["duplicate_count"] == 120
    assert execute(client, project, "message.send", args).status_code == 429
    assert command(client, project, "message.send", args, key="first") == first
    command(client, project, "message.ack", {"stage": "received"}, first["id"], agent["token"])
    assert len(get(client, project, "inbox", agent["token"]).json()["items"]) == 1


def test_reply_thread_limit_and_human_identity_boundary(client, project):
    agent = enroll(client, project, role="owner")
    actor_path = f"/api/v1/projects/{project['project']['id']}/actors"
    response = client.post(
        actor_path, headers=headers(agent["token"]), json={"name": "Pretend human", "kind": "human"}
    )
    assert response.status_code == 403
    previous = None
    for index in range(8):
        previous = command(
            client,
            project,
            "message.send",
            {
                "body": f"Thread step {index}",
                "reply_to_id": previous["id"] if previous else None,
            },
        )
    assert (
        execute(
            client,
            project,
            "message.send",
            {
                "body": "Another automated reply",
                "reply_to_id": previous["id"],
            },
        ).json()["code"]
        == "reply_limit"
    )


def test_expired_claim_cannot_receive_human_permission(client, project, app):
    agent = enroll(client, project)
    artifact = upload(client, project)
    task = command(
        client, project, "artifact.review.request", ident=artifact["id"], token=agent["token"]
    )
    with app.state.core.sessions.begin() as db:
        db.get(m.Task, task["id"]).lease_until = datetime.now(UTC) - timedelta(seconds=1)
    response = execute(
        client,
        project,
        "artifact.review.decide",
        {
            "expected_version": task["version"],
            "decision": "approved",
        },
        task["id"],
    )
    assert response.status_code == 409 and response.json()["code"] == "lease_invalid"


def test_attention_bounds_large_bodies_and_full_resource_keeps_evidence(client, project):
    agent = enroll(client, project)
    body = "Synthetic evidence " * 4000 + "END-OF-EVIDENCE"
    for index in range(3):
        command(
            client,
            project,
            "message.send",
            {"body": body + str(index), "recipient_id": agent["actor"]["id"]},
        )
    packet = get(client, project, "attention", agent["token"], max_chars=4000).json()
    assert len(json.dumps(packet, ensure_ascii=False, indent=2)) <= 4000
    assert packet["has_more"] and packet["items"][0]["body_truncated"]
    full = get(client, project, packet["items"][0]["resource"], agent["token"]).json()
    assert "END-OF-EVIDENCE" in full["body"] and full["ack_stage"] is None
