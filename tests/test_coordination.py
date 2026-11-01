from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from team_sync import models as m
from team_sync.core import DomainError

from .conftest import enroll, execute, headers, initialize


def test_credentials_and_cross_project_references(client, project, app):
    other = initialize(client)
    root = f"/api/v1/projects/{project['project']['id']}"
    assert client.get(root + "/snapshot").status_code == 401
    assert client.get(root + "/snapshot", headers=headers(other["token"])).status_code == 404
    assert (
        client.get(root + "/snapshot", headers=headers("test-bootstrap-secret")).status_code == 401
    )
    challenge = execute(client, other, "challenge.create", {"title": "Other challenge"}).json()
    response = execute(
        client,
        project,
        "task.create",
        {
            "title": "Invalid link",
            "challenge_id": challenge["id"],
        },
    )
    assert response.status_code == 404
    # Isolation remains true if a future adapter accidentally bypasses domain validation.
    with pytest.raises(IntegrityError), app.state.core.sessions.begin() as db:
        db.add(
            m.Task(
                project_id=project["project"]["id"], title="Invalid", challenge_id=challenge["id"]
            )
        )


def test_roles_and_validation(client, project):
    observer = enroll(client, project, role="observer")
    assert (
        execute(
            client, project, "task.create", {"title": "Forbidden"}, token=observer["token"]
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/projects/{project['project']['id']}/actors",
            headers=headers(observer["token"]),
            json={"name": "Escalate", "role": "owner"},
        ).status_code
        == 403
    )
    assert execute(client, project, "task.create", {"title": "   "}).status_code == 422
    assert execute(client, project, "task.claim", {"expected_version": 1}).status_code == 422
    assert (
        execute(
            client,
            project,
            "flag.create",
            {
                "value": "FLAG{x}",
                "expires_at": "2026-11-01T12:00:00",
            },
        ).status_code
        == 422
    )


def test_atomic_claim_between_two_agents(client, project, app):
    first, second = enroll(client, project, "A"), enroll(client, project, "B")
    task = execute(client, project, "task.create", {"title": "Analyze service"}).json()
    core, barrier = app.state.core, Barrier(2)

    def claim(agent):
        barrier.wait(timeout=5)
        try:
            return core.execute(
                project["project"]["id"],
                agent["token"],
                "task.claim",
                {"expected_version": 1},
                task["id"],
                str(uuid4()),
            )
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [first, second]))
    assert sum(isinstance(result, dict) for result in results) == 1
    loser = next(result for result in results if isinstance(result, DomainError))
    assert loser.status == 409
    events = core.events(project["project"]["id"], project["token"])
    assert sum(event["type"] == "task.claim" for event in events["items"]) == 1
    assert [event["seq"] for event in events["items"]] == list(range(1, events["head"] + 1))


def test_expiry_takeover_and_fencing(client, project, app):
    first, second = enroll(client, project, "A"), enroll(client, project, "B")
    task = execute(client, project, "task.create", {"title": "Work"}).json()
    claimed = execute(
        client, project, "task.claim", {"expected_version": 1}, task["id"], first["token"]
    ).json()
    with app.state.core.sessions.begin() as db:
        db.execute(
            update(m.Task)
            .where(m.Task.id == task["id"])
            .values(lease_until=m.now() - timedelta(seconds=1))
        )
    assert (
        execute(
            client,
            project,
            "task.renew",
            {"expected_version": claimed["version"]},
            task["id"],
            first["token"],
        ).status_code
        == 409
    )
    takeover = execute(
        client,
        project,
        "task.claim",
        {"expected_version": claimed["version"]},
        task["id"],
        second["token"],
    ).json()
    # Even knowing the new version cannot grant the old actor ownership.
    assert (
        execute(
            client,
            project,
            "task.complete",
            {
                "expected_version": takeover["version"],
                "result": "Stale result",
            },
            task["id"],
            first["token"],
        ).status_code
        == 409
    )
    renewed = execute(
        client,
        project,
        "task.renew",
        {
            "expected_version": takeover["version"],
        },
        task["id"],
        second["token"],
    ).json()
    done = execute(
        client,
        project,
        "task.complete",
        {
            "expected_version": renewed["version"],
            "result": "Evidence and result",
        },
        task["id"],
        second["token"],
    )
    assert done.status_code == 200
    assert done.json()["status"] == "done"


def test_idempotency_and_transaction_rollback(client, project, app):
    core, pid = app.state.core, project["project"]["id"]
    before = core.events(pid, project["token"])["head"]
    first = execute(client, project, "task.create", {"title": "Once"}, key="retry-1")
    repeat = execute(client, project, "task.create", {"title": "Once"}, key="retry-1")
    assert first.json() == repeat.json()
    assert core.events(pid, project["token"])["head"] == before + 1
    conflict = execute(client, project, "task.create", {"title": "Different"}, key="retry-1")
    assert conflict.status_code == 409
    assert core.events(pid, project["token"])["head"] == before + 1
    # A failed event insert must roll back its associated domain mutation as well.
    from unittest.mock import patch

    with patch.object(core, "emit", side_effect=RuntimeError("Simulated write failure")):
        with pytest.raises(RuntimeError):
            core.execute(pid, project["token"], "task.create", {"title": "Rollback"}, None, "bad")
    assert len(core.listing(pid, project["token"], "tasks")) == 1
    assert core.events(pid, project["token"])["head"] == before + 1


def test_flags_verification_submission_and_scope(client, project, app):
    worker = enroll(client, project)
    flag = execute(
        client,
        project,
        "flag.create",
        {
            "value": "FLAG{sample}",
            "target": "team-1",
            "round": "1",
        },
        token=worker["token"],
    ).json()
    assert flag["verification_status"] == "unverified"
    assert (
        execute(
            client,
            project,
            "flag.create",
            {
                "value": "FLAG{sample}",
                "target": "team-1",
                "round": "1",
            },
        ).status_code
        == 409
    )
    assert (
        execute(
            client,
            project,
            "flag.create",
            {
                "value": "FLAG{sample}",
                "target": "team-1",
                "round": "2",
            },
        ).status_code
        == 200
    )
    verification = {
        "expected_version": 1,
        "status": "confirmed",
        "method": "manual",
        "evidence": "Reviewed checker output",
    }
    assert (
        execute(
            client, project, "flag.verify", verification, flag["id"], worker["token"]
        ).status_code
        == 403
    )
    assert execute(client, project, "flag.verify", verification, flag["id"]).status_code == 200
    submission = execute(
        client,
        project,
        "submission.create",
        {
            "outcome": "unknown",
            "evidence": "Connection lost before response",
        },
        flag["id"],
        worker["token"],
    )
    assert submission.status_code == 200
    flags = app.state.core.listing(project["project"]["id"], project["token"], "flags")
    assert (
        next(item for item in flags if item["id"] == flag["id"])["verification_status"]
        == "confirmed"
    )
    correction = execute(
        client,
        project,
        "flag.verify",
        {
            "expected_version": 2,
            "status": "rejected",
            "method": "platform",
            "evidence": "Later authoritative check rejected the candidate",
        },
        flag["id"],
    )
    assert correction.status_code == 200
    history = app.state.core.listing(project["project"]["id"], project["token"], "verifications")
    assert [item["status"] for item in history] == ["confirmed", "rejected"]
    assert history[0]["evidence"] == "Reviewed checker output"
    disabled = initialize(client, capabilities={"flags": False})
    assert execute(client, disabled, "flag.create", {"value": "FLAG{x}"}).status_code == 409


def test_direct_messages_receipts_and_event_redaction(client, project, app):
    first, second = enroll(client, project, "A"), enroll(client, project, "B")
    message = execute(
        client,
        project,
        "message.send",
        {
            "recipient_id": first["actor"]["id"],
            "body": "Private finding details",
        },
    ).json()
    core, pid = app.state.core, project["project"]["id"]
    assert core.inbox(pid, second["token"]) == []
    assert core.inbox(pid, first["token"])[0]["ack_stage"] is None
    assert (
        execute(
            client, project, "message.ack", {"stage": "processed"}, message["id"], second["token"]
        ).status_code
        == 404
    )
    assert (
        execute(
            client, project, "message.ack", {"stage": "received"}, message["id"], first["token"]
        ).status_code
        == 200
    )
    assert (
        execute(
            client, project, "message.ack", {"stage": "processed"}, message["id"], first["token"]
        ).status_code
        == 200
    )
    assert (
        execute(
            client, project, "message.ack", {"stage": "received"}, message["id"], first["token"]
        ).status_code
        == 409
    )
    assert core.inbox(pid, first["token"])[0]["ack_stage"] == "processed"
    assert "Private finding details" not in str(core.events(pid, second["token"]))
    with core.sessions() as db:
        hashes = list(db.scalars(select(m.Actor.token_hash).where(m.Actor.project_id == pid)))
    assert first["token"] not in hashes
    assert all(len(value) == 64 for value in hashes)


def test_artifacts_are_immutable_and_project_scoped(client, project, app):
    import hashlib

    pid = project["project"]["id"]
    root = f"/api/v1/projects/{pid}/artifacts"
    payload = b"CTF binary\x00\xff"
    auth = {**headers(project["token"]), "Idempotency-Key": "upload-once"}
    uploaded = client.post(root, params={"name": "sample.bin"}, content=payload, headers=auth)
    assert uploaded.status_code == 201, uploaded.text
    artifact = uploaded.json()
    repeated = client.post(root, params={"name": "sample.bin"}, content=payload, headers=auth)
    assert repeated.json() == artifact
    changed = client.post(root, params={"name": "sample.bin"}, content=b"different", headers=auth)
    assert changed.status_code == 409
    downloaded = client.get(root + f"/{artifact['id']}/content", headers=headers(project["token"]))
    assert downloaded.content == payload
    assert downloaded.headers["X-Content-SHA256"] == hashlib.sha256(payload).hexdigest()
    assert (
        client.post(root, params={"name": "../escape"}, content=payload, headers=auth).status_code
        == 422
    )
    other = initialize(client)
    assert (
        client.get(root + f"/{artifact['id']}/content", headers=headers(other["token"])).status_code
        == 404
    )
    limited_app = app.state.core.settings
    object.__setattr__(limited_app, "max_artifact_bytes", 2)
    assert (
        client.post(root, params={"name": "too-large"}, content=payload, headers=auth).status_code
        == 413
    )
    assert len(app.state.core.listing(pid, project["token"], "artifacts")) == 1


def test_credential_revocation_and_last_owner(client, project):
    agent = enroll(client, project)
    root = f"/api/v1/projects/{project['project']['id']}"
    response = client.post(
        root + f"/actors/{agent['actor']['id']}/revoke", headers=headers(project["token"])
    )
    assert response.status_code == 200
    assert client.get(root + "/snapshot", headers=headers(agent["token"])).status_code == 401
    response = client.post(
        root + f"/actors/{project['actor']['id']}/revoke", headers=headers(project["token"])
    )
    assert response.status_code == 409


def test_event_replay_pagination_and_wait(client, project):
    root = f"/api/v1/projects/{project['project']['id']}/events"
    auth = headers(project["token"])
    first = client.get(root, params={"limit": 1}, headers=auth).json()
    for index in range(3):
        execute(client, project, "task.create", {"title": f"Task {index}"})
    second = client.get(
        root, params={"after": first["next_cursor"], "limit": 2}, headers=auth
    ).json()
    third = client.get(root, params={"after": second["next_cursor"]}, headers=auth).json()
    assert [item["seq"] for item in second["items"] + third["items"]] == [2, 3, 4]
    empty = client.get(root + "/wait", params={"after": 4, "timeout": 0}, headers=auth).json()
    assert empty["items"] == []
    assert empty["next_cursor"] == 4
    assert client.get(root, params={"after": 999}, headers=auth).status_code == 409


def test_finding_scope_and_negative_results(client, project):
    one = execute(client, project, "challenge.create", {"title": "One"}).json()
    two = execute(client, project, "challenge.create", {"title": "Two"}).json()
    task = execute(
        client,
        project,
        "task.create",
        {
            "title": "Check hypothesis",
            "challenge_id": one["id"],
        },
    ).json()
    data = {
        "title": "Attempt",
        "body": "Did not work under these conditions",
        "evidence": "artifact:log-id",
        "kind": "failed_attempt",
        "task_id": task["id"],
    }
    assert (
        execute(
            client,
            project,
            "finding.create",
            {
                **data,
                "challenge_id": two["id"],
            },
        ).status_code
        == 422
    )
    assert (
        execute(
            client,
            project,
            "finding.create",
            {
                **data,
                "challenge_id": one["id"],
            },
        ).status_code
        == 200
    )
