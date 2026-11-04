"""Editing preserves provenance, scope, versions, decisions and recipient acknowledgements."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from team_sync import models as m

from .conftest import enroll, execute, headers, initialize
from .test_human_workflows import approved_review, command, get, upload


def edit(client, project, collection, item, changes, token=None, key=None):
    return execute(
        client,
        project,
        "resource.update",
        {
            "collection": collection,
            "expected_version": item["version"],
            "changes": changes,
            "reason": "Correction with evidence",
        },
        item["id"],
        token,
        key,
    )


@pytest.mark.parametrize(
    "collection",
    [
        "projects",
        "actors",
        "challenges",
        "tasks",
        "findings",
        "flags",
        "verifications",
        "submissions",
        "messages",
        "artifacts",
        "activities",
    ],
)
def test_all_working_resources_have_versioned_edits_and_history(client, project, collection):
    agent = enroll(client, project)
    token = project["token"]
    if collection == "projects":
        item, changes = project["project"], {"name": "Corrected CTF", "rules": "Updated rules"}
    elif collection == "actors":
        item, changes = agent["actor"], {"name": "Named specialist", "capabilities": ["crypto"]}
        token = agent["token"]
    elif collection == "challenges":
        item = command(client, project, "challenge.create", {"title": "Challenge"})
        changes = {"title": "Corrected challenge", "category": "web"}
        token = agent["token"]
    elif collection == "tasks":
        item = command(client, project, "task.create", {"title": "Task"})
        changes = {"title": "Corrected task", "description": "New context"}
    elif collection == "findings":
        item = command(
            client,
            project,
            "finding.create",
            {"title": "README", "body": "Old report", "evidence": "A file"},
            token=agent["token"],
        )
        changes = {"body": "Corrected report", "evidence": "Actual file reference"}
        token = agent["token"]
    elif collection in {"flags", "verifications", "submissions"}:
        flag = command(client, project, "flag.create", {"value": "CTF{original}"})
        if collection == "flags":
            item, changes = flag, {"value": "CTF{corrected}"}
        elif collection == "verifications":
            command(
                client,
                project,
                "flag.verify",
                {
                    "expected_version": 1,
                    "status": "confirmed",
                    "method": "manual",
                    "evidence": "Original check",
                },
                flag["id"],
            )
            item = get(client, project, "collections/verifications").json()["items"][0]
            changes = {"evidence": "Corrected verification evidence"}
        else:
            item = command(
                client,
                project,
                "submission.create",
                {"outcome": "unknown", "evidence": "Pending"},
                flag["id"],
            )
            changes = {"outcome": "accepted", "evidence": "Platform receipt"}
    elif collection == "messages":
        item = command(
            client,
            project,
            "message.send",
            {"recipient_id": agent["actor"]["id"], "body": "Before"},
        )
        changes = {"body": "Corrected message"}
    elif collection == "artifacts":
        item, changes = upload(client, project), {"name": "corrected.txt"}
    else:
        item = command(
            client,
            project,
            "activity.report",
            {"state": "working", "detail": "Old step"},
            token=agent["token"],
        )
        changes = {"state": "blocked", "detail": "Waiting for input"}
        token = agent["token"]
    key = str(uuid4())
    response = edit(client, project, collection, item, changes, token, key)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["version"] == item["version"] + 1
    for field, value in changes.items():
        assert updated[field] == value
    assert edit(client, project, collection, item, changes, token, key).json() == updated
    assert edit(client, project, collection, item, changes, token).status_code == 409
    path = f"resources/{collection}/{item['id']}/history"
    history = get(client, project, path, token).json()
    assert history["total"] == 1
    full = get(client, project, path, token, revision_id=history["items"][0]["id"]).json()
    assert full["before"]["version"] == item["version"]
    assert full["after"]["version"] == updated["version"]
    assert full["editor_id"] == (
        agent["actor"]["id"] if token == agent["token"] else project["actor"]["id"]
    )
    assert "token_hash" not in full["before"]
    if "author_id" in item:
        assert full["before"]["author_id"] == full["after"]["author_id"]


def test_edits_enforce_roles_last_owner_and_scope(client, project):
    worker, peer = enroll(client, project), enroll(client, project)
    assert (
        edit(
            client, project, "actors", worker["actor"], {"role": "owner"}, worker["token"]
        ).status_code
        == 403
    )
    assert (
        edit(client, project, "actors", project["actor"], {"role": "worker"}).json()["code"]
        == "last_owner"
    )
    assert edit(client, project, "actors", worker["actor"], {"kind": "human"}).status_code == 422
    finding = command(
        client,
        project,
        "finding.create",
        {"title": "Peer evidence", "body": "Before", "evidence": "Original"},
        token=peer["token"],
    )
    assert (
        edit(
            client, project, "findings", finding, {"body": "Overwrite"}, worker["token"]
        ).status_code
        == 403
    )
    other = initialize(client)
    foreign = command(client, other, "challenge.create", {"title": "Other CTF"})
    assert (
        edit(client, project, "findings", finding, {"challenge_id": foreign["id"]}).status_code
        == 404
    )
    response = execute(
        client,
        project,
        "resource.update",
        {"collection": "findings", "expected_version": 1, "changes": {"body": "Foreign write"}},
        finding["id"],
        other["token"],
    )
    assert response.status_code == 404


def test_flag_corrections_reset_confirmation_without_rewriting_platform_history(client, project):
    flag = command(client, project, "flag.create", {"value": "CTF{old}"})
    confirmed = command(
        client,
        project,
        "flag.verify",
        {
            "expected_version": 1,
            "status": "confirmed",
            "method": "platform",
            "evidence": "Checked old value",
        },
        flag["id"],
    )
    submission = command(
        client,
        project,
        "submission.create",
        {"outcome": "accepted", "evidence": "Old value accepted"},
        flag["id"],
    )
    changed = edit(client, project, "flags", confirmed, {"value": "CTF{new}"}).json()
    assert changed["verification_status"] == "unverified" and changed["verification"] is None
    checks = get(client, project, "collections/verifications").json()["items"]
    assert checks[0]["subject"]["value"] == "CTF{old}"
    assert (
        get(client, project, f"resources/submissions/{submission['id']}").json()["item"]["subject"][
            "value"
        ]
        == "CTF{old}"
    )
    correction = edit(client, project, "verifications", checks[0], {"status": "rejected"})
    assert correction.status_code == 200
    current = get(client, project, f"resources/flags/{flag['id']}").json()["item"]
    assert current["verification_status"] == "unverified"
    duplicate = command(client, project, "flag.create", {"value": "CTF{duplicate}"})
    assert edit(client, project, "flags", current, {"value": duplicate["value"]}).status_code == 409


def test_message_edits_require_new_read_version_without_leaking_private_history(client, project):
    recipient, outsider = enroll(client, project), enroll(client, project)
    message = command(
        client,
        project,
        "message.send",
        {"recipient_id": recipient["actor"]["id"], "body": "Original"},
    )
    command(
        client, project, "message.ack", {"stage": "processed"}, message["id"], recipient["token"]
    )
    changed = edit(client, project, "messages", message, {"body": "Corrected"}).json()
    assert (
        get(client, project, f"messages/{message['id']}", recipient["token"]).json()["ack_stage"]
        is None
    )
    assert get(client, project, "outbox").json()["items"][0]["recipient_ack_stage"] is None
    assert (
        execute(
            client,
            project,
            "message.ack",
            {"stage": "processed"},
            message["id"],
            recipient["token"],
        ).json()["code"]
        == "message_version_required"
    )
    assert (
        execute(
            client,
            project,
            "message.ack",
            {"stage": "processed", "expected_version": 1},
            message["id"],
            recipient["token"],
        ).status_code
        == 409
    )
    command(
        client,
        project,
        "message.ack",
        {"stage": "received", "expected_version": changed["version"]},
        message["id"],
        recipient["token"],
    )
    assert get(client, project, "outbox").json()["items"][0]["recipient_ack_stage"] == "received"
    path = f"resources/messages/{message['id']}/history"
    assert get(client, project, path, outsider["token"]).status_code == 404
    assert get(client, project, path, recipient["token"]).json()["total"] == 1
    assert (
        edit(
            client, project, "messages", changed, {"body": "Recipient edit"}, recipient["token"]
        ).status_code
        == 403
    )


def test_artifact_revisions_keep_bytes_and_need_their_own_permission(client, project):
    agent = enroll(client, project)
    original = upload(client, project, content=b"Original evidence")
    approved_review(client, project, agent, original)
    path = f"/api/v1/projects/{project['project']['id']}/artifacts"
    args = {
        "name": "revised.txt",
        "supersedes_id": original["id"],
        "expected_version": original["version"],
    }
    response = client.post(
        path,
        headers={**headers(project["token"]), "Idempotency-Key": "new-content"},
        params=args,
        content=b"Revised evidence",
    )
    assert response.status_code == 201, response.text
    revised = response.json()
    assert revised["supersedes_id"] == original["id"] and revised["requires_review"]
    assert revised["sha256"] != original["sha256"]
    assert (
        client.post(
            path,
            headers={**headers(project["token"]), "Idempotency-Key": "new-content"},
            params=args,
            content=b"Revised evidence",
        ).json()
        == revised
    )
    assert (
        get(client, project, f"artifacts/{original['id']}/content", agent["token"]).content
        == b"Original evidence"
    )
    assert (
        get(client, project, f"artifacts/{revised['id']}/content", agent["token"]).status_code
        == 403
    )
    assert get(client, project, f"artifacts/{revised['id']}/content").content == b"Revised evidence"
    assert (
        edit(
            client, project, "artifacts", revised, {"requires_review": False}, agent["token"]
        ).status_code
        == 403
    )
    newer_args = {**args, "expected_version": original["version"] + 1}
    response = client.post(
        path,
        headers={**headers(project["token"]), "Idempotency-Key": "branch"},
        params=newer_args,
        content=b"Branch",
    )
    assert response.status_code == 409 and response.json()["code"] == "artifact_superseded"


def test_task_edits_preserve_lease_and_move_related_evidence_consistently(client, project):
    worker, peer = enroll(client, project), enroll(client, project)
    first = command(client, project, "challenge.create", {"title": "First"})
    second = command(client, project, "challenge.create", {"title": "Second"})
    task = command(
        client,
        project,
        "task.create",
        {"title": "Move task", "challenge_id": first["id"]},
        token=worker["token"],
    )
    claimed = command(
        client, project, "task.claim", {"expected_version": 1}, task["id"], worker["token"]
    )
    finding = command(
        client,
        project,
        "finding.create",
        {
            "title": "Peer evidence",
            "body": "Original",
            "evidence": "Shared",
            "challenge_id": first["id"],
            "task_id": task["id"],
        },
        token=peer["token"],
    )
    changed = edit(
        client, project, "tasks", claimed, {"challenge_id": second["id"]}, worker["token"]
    ).json()
    assert (
        changed["assignee_id"] == worker["actor"]["id"]
        and changed["lease_until"] == claimed["lease_until"]
    )
    evidence = get(client, project, f"resources/findings/{finding['id']}").json()["item"]
    assert evidence["challenge_id"] == second["id"] and evidence["author_id"] == peer["actor"]["id"]
    assert (
        edit(
            client, project, "tasks", changed, {"status": "done", "result": "Force other worker"}
        ).status_code
        == 409
    )


def test_edit_rollback_and_concurrent_versions_are_atomic(client, project, app, monkeypatch):
    item = command(
        client,
        project,
        "finding.create",
        {"title": "Atomic edit", "body": "Original", "evidence": "Test"},
    )
    head = get(client, project, "events").json()["head"]
    emit = app.state.core.emit

    def fail(db, proj, actor, kind, payload):
        if kind == "finding.update":
            raise RuntimeError("Simulated failure after revision")
        return emit(db, proj, actor, kind, payload)

    with monkeypatch.context() as patch:
        patch.setattr(app.state.core, "emit", fail)
        with pytest.raises(RuntimeError):
            edit(client, project, "findings", item, {"body": "Changed"})
    assert get(client, project, "events").json()["head"] == head
    assert get(client, project, f"resources/findings/{item['id']}/history").json()["total"] == 0

    def update(body):
        return edit(client, project, "findings", item, {"body": body}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["First", "Second"]))
    assert sorted(results) == [200, 409]
    assert get(client, project, f"resources/findings/{item['id']}/history").json()["total"] == 1


def test_revision_foreign_keys_enforce_project_scope(app, client, project):
    other = initialize(client)
    item = command(
        client, project, "finding.create", {"title": "Scoped", "body": "Body", "evidence": "Test"}
    )
    with pytest.raises(IntegrityError), app.state.core.sessions.begin() as db:
        db.add(
            m.Revision(
                project_id=project["project"]["id"],
                collection="findings",
                resource_id=item["id"],
                finding_id=item["id"],
                editor_id=other["actor"]["id"],
                before={},
                after={},
                reason="Must fail",
            )
        )
        db.flush()
    with app.state.core.sessions() as db:
        assert not db.scalar(select(m.Revision).where(m.Revision.resource_id == item["id"]))
