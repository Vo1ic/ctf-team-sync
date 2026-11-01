"""Bounded agent views, evaluated inside Core's authenticated project transaction."""

import json
import math
from typing import get_args

from sqlalchemy import and_, func, or_, select

from team_sync import models as m
from team_sync.core import record
from team_sync.schemas import AgentAction

MODELS = {
    "projects": m.Project,
    "actors": m.Actor,
    "challenges": m.Challenge,
    "tasks": m.Task,
    "findings": m.Finding,
    "flags": m.Flag,
    "verifications": m.Verification,
    "submissions": m.Submission,
    "artifacts": m.Artifact,
    "messages": m.Message,
}


def operations(role):
    if role == "observer":
        return ["message.ack"]
    return [
        name
        for name in get_args(AgentAction.model_fields["operation"].annotation)
        if role != "worker" or name != "flag.verify"
    ]


def clip(value, size=180):
    return value[:size] + "…" if value and len(value) > size else value


def brief(item, timestamp=None):
    value = record(item)
    for field in ("description", "body", "evidence", "result", "value", "name", "title", "rules"):
        if isinstance(value.get(field), str):
            value[field] = clip(value[field])
    value.pop("verification", None)
    if "capabilities" in value and isinstance(value["capabilities"], list):
        value["capabilities"] = [clip(c, 60) for c in value["capabilities"][:12]]
    if isinstance(item, m.Task) and timestamp:
        remaining = (
            max(0, math.ceil((item.lease_until - timestamp).total_seconds()))
            if item.lease_until
            else 0
        )
        value["lease_remaining_seconds"] = remaining
        value["can_claim"] = item.status != "done" and remaining == 0
    value["resource"] = f"{item.__tablename__}/{item.id}"
    return value


def page(db, stmt, render, limit=5, offset=0):
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    return {
        "items": [render(row) for row in db.execute(stmt.offset(offset).limit(limit)).all()],
        "total": total,
        "has_more": offset + limit < total,
    }


def messages(project, actor):
    return (
        select(m.Message, m.Receipt.stage)
        .outerjoin(
            m.Receipt,
            and_(
                m.Receipt.project_id == project.id,
                m.Receipt.message_id == m.Message.id,
                m.Receipt.actor_id == actor.id,
            ),
        )
        .where(
            m.Message.project_id == project.id,
            or_(m.Message.recipient_id == actor.id, m.Message.recipient_id.is_(None)),
        )
    )


def sent_messages(project, actor):
    return (
        select(m.Message, m.Receipt.stage)
        .outerjoin(
            m.Receipt,
            and_(
                m.Receipt.project_id == project.id,
                m.Receipt.message_id == m.Message.id,
                m.Receipt.actor_id == m.Message.recipient_id,
            ),
        )
        .where(m.Message.project_id == project.id, m.Message.sender_id == actor.id)
    )


def workspace(core, db, project, actor, query):
    timestamp = db.scalar(select(func.clock_timestamp()))
    challenge_id = query.challenge_id
    if challenge_id:
        core.get(db, m.Challenge, project.id, challenge_id)
    if query.task_id:
        task = core.get(db, m.Task, project.id, query.task_id)
        if challenge_id and task.challenge_id != challenge_id:
            from team_sync.core import fail

            fail(422, "scope_mismatch", "Task does not belong to the requested challenge")
        challenge_id = task.challenge_id

    def scoped(model):
        stmt = select(model).where(model.project_id == project.id)
        if challenge_id and hasattr(model, "challenge_id"):
            stmt = stmt.where(model.challenge_id == challenge_id)
        if query.task_id and model is m.Task:
            stmt = stmt.where(model.id == query.task_id)
        return stmt

    def section(stmt):
        def render(row):
            item = row[0]
            value = brief(item, timestamp)
            if isinstance(item, m.Task):
                actions = []
                if actor.role != "observer":
                    if value["can_claim"]:
                        actions = ["claim"]
                    elif item.status == "running" and item.assignee_id == actor.id:
                        actions = ["renew", "complete", "release"]
                value["available_actions"] = actions
                value["can_claim"] = "claim" in actions
            return value

        return page(db, stmt, render, query.limit)

    available = or_(
        m.Task.status == "open", and_(m.Task.status == "running", m.Task.lease_until <= timestamp)
    )
    own = scoped(m.Task).where(m.Task.assignee_id == actor.id, m.Task.status == "running")
    unread = messages(project, actor).where(
        or_(m.Receipt.stage.is_(None), m.Receipt.stage != "processed")
    )
    findings = scoped(m.Finding)
    if query.task_id:
        findings = findings.where(m.Finding.task_id == query.task_id)
    content = {
        "my_tasks": section(own.order_by(m.Task.lease_until, m.Task.id)),
        "messages": page(
            db,
            unread.order_by(m.Message.created_at, m.Message.id),
            lambda row: {**brief(row[0]), "ack_stage": row[1]},
            query.limit,
        ),
        "available_tasks": section(
            scoped(m.Task).where(available).order_by(m.Task.created_at, m.Task.id)
        ),
        "team_tasks": section(
            scoped(m.Task)
            .where(
                m.Task.status == "running",
                m.Task.assignee_id != actor.id,
                m.Task.lease_until > timestamp,
            )
            .order_by(m.Task.created_at, m.Task.id)
        ),
        "recent_findings": section(
            findings.order_by(m.Finding.created_at.desc(), m.Finding.id.desc())
        ),
        "sent_messages": page(
            db,
            sent_messages(project, actor).order_by(
                m.Message.created_at.desc(), m.Message.id.desc()
            ),
            lambda row: {**brief(row[0]), "recipient_ack_stage": row[1]},
            query.limit,
        ),
    }
    if project.capabilities.get("flags"):
        content["flags_to_review"] = section(
            scoped(m.Flag)
            .where(m.Flag.verification_status == "unverified")
            .order_by(m.Flag.created_at, m.Flag.id)
        )
    result = {
        "schema_version": 1,
        "server_time": timestamp.isoformat(),
        "project": {
            "id": project.id,
            "name": clip(project.name, 120),
            "rules": clip(project.rules, 600),
            "rules_truncated": len(project.rules) > 600,
            "capabilities": project.capabilities,
        },
        "self": brief(actor),
        "events_after": project.event_seq,
        "scope": {"challenge_id": challenge_id, "task_id": query.task_id},
        "allowed_operations": operations(actor.role),
        "can_manage_access": actor.role == "owner",
        "workflow": [
            "Read messages and task details before choosing work; read does not mark processed.",
            "Claim available work with its current version; renew your lease before it expires.",
            "Use resources/get or get_resource for complete text; these sections are excerpts.",
            "If rules_truncated, read resources/projects/<project_id> before acting on scope.",
            "Publish findings with evidence; mark processed only after acting. "
            "Release a live task with a reason when handing it off.",
            "Retry an uncertain mutation with the SAME idempotency_key and arguments; "
            "reread current state after a version conflict.",
            "When has_more is true, fetch remaining pages. events_after is a snapshot "
            "watermark, not an acknowledgement or a guarantee of full message delivery.",
        ],
        "sections": content,
        "truncated": False,
    }
    # Preserve totals and explicit omission markers even when a small model asks for less context.
    for name in reversed(content):
        while (
            content[name]["items"]
            and len(json.dumps(result, ensure_ascii=False, indent=2)) > query.max_chars
        ):
            content[name]["items"].pop()
            content[name]["has_more"] = True
            result["truncated"] = True
    # Arbitrary project capability names can be large; do not let them defeat the budget.
    if len(json.dumps(result, ensure_ascii=False, indent=2)) > query.max_chars:
        result["project"]["capabilities"] = {"flags": bool(project.capabilities.get("flags"))}
        result["project"]["rules"] = ""
        result["project"]["rules_truncated"] = bool(project.rules)
        result["project"]["name"] = clip(project.name, 40)
        result["self"]["name"] = clip(actor.name, 40)
        result["self"]["capabilities"] = []
        result["truncated"] = True
    return result


def resource(core, db, project, actor, collection, resource_id, offset=0, limit=10):
    if collection == "projects":
        if resource_id != project.id:
            from team_sync.core import fail

            fail(404, "not_found", "Project not found")
        item = project
    else:
        item = core.get(db, MODELS[collection], project.id, resource_id)
    if collection == "messages" and item.recipient_id not in (None, actor.id):
        from team_sync.core import fail

        fail(404, "not_found", "Message not found")
    value = record(item)
    if collection == "messages":
        receipt = db.get(m.Receipt, (project.id, item.id, actor.id))
        value["ack_stage"] = receipt.stage if receipt else None
    related = {}

    def add(name, predicate):
        model = MODELS[name]
        related[name] = page(
            db,
            select(model)
            .where(model.project_id == project.id, predicate)
            .order_by(model.created_at, model.id),
            lambda row: brief(row[0]),
            limit,
            offset,
        )

    if collection == "tasks":
        add("findings", m.Finding.task_id == item.id)
    if collection == "challenges":
        add("tasks", m.Task.challenge_id == item.id)
        add("findings", m.Finding.challenge_id == item.id)
        add("flags", m.Flag.challenge_id == item.id)
    if collection == "flags":
        add("verifications", m.Verification.flag_id == item.id)
        add("submissions", m.Submission.flag_id == item.id)
    return {
        "collection": collection,
        "item": value,
        "related": related,
        "events_after": project.event_seq,
    }


def search(db, project, actor, query):
    found, total = [], 0
    names = (
        [query.collection]
        if query.collection
        else ["challenges", "tasks", "findings", "artifacts", "messages", "flags"]
    )
    escaped = query.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    for name in names:
        model = MODELS[name]
        fields = [
            getattr(model, field)
            for field in (
                "title",
                "name",
                "description",
                "body",
                "evidence",
                "result",
                "value",
                "rules",
            )
            if hasattr(model, field)
        ]
        if not fields:
            continue
        stmt = select(model).where(
            (model.id == project.id) if model is m.Project else (model.project_id == project.id),
            or_(*(field.ilike(f"%{escaped}%", escape="\\") for field in fields)),
        )
        if model is m.Message:
            stmt = stmt.where(
                or_(m.Message.recipient_id == actor.id, m.Message.recipient_id.is_(None))
            )
        batch = page(
            db,
            stmt.order_by(model.created_at.desc(), model.id.desc()),
            lambda row, name=name: {"collection": name, "item": brief(row[0])},
            query.offset + query.limit,
        )
        found.extend(batch["items"])
        total += batch["total"]
    found.sort(key=lambda row: (row["item"]["created_at"], row["item"]["id"]), reverse=True)
    return {
        "items": found[query.offset : query.offset + query.limit],
        "total": total,
        "has_more": query.offset + query.limit < total,
        "events_after": project.event_seq,
    }
