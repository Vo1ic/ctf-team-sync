"""Bounded attention without changing the durable message/event history."""

import json
from datetime import timedelta

from sqlalchemy import and_, case, exists, func, or_, select

from team_sync import models as m
from team_sync.domain import digest, fail, record


def create_message(core, db, project, actor, data):
    timestamp = db.scalar(select(func.clock_timestamp()))
    if data.recipient_id:
        target = core.get(db, m.Actor, project.id, data.recipient_id)
        if target.id == actor.id or not target.active:
            fail(422, "invalid_recipient", "Select another active participant")
    if data.task_id:
        core.get(db, m.Task, project.id, data.task_id)
    if data.expires_at and data.expires_at <= timestamp:
        fail(422, "message_expired", "Expiration must be in the future")
    parent_id, depth = data.reply_to_id, 0
    while parent_id:
        parent = core.get(db, m.Message, project.id, parent_id)
        if parent.recipient_id not in (None, actor.id) and parent.sender_id != actor.id:
            fail(404, "not_found", "Reply message not found")
        parent_id, depth = parent.reply_to_id, depth + 1
        if depth >= 8:
            fail(409, "reply_limit", "Summarize this thread before starting a new exchange")
    sends = select(m.Event).where(
        m.Event.project_id == project.id,
        m.Event.actor_id == actor.id,
        m.Event.created_at > timestamp - timedelta(seconds=60),
        m.Event.type.in_(["message.send", "message.duplicate"]),
    )
    if db.scalar(select(func.count()).select_from(sends.subquery())) >= 120:
        fail(429, "message_rate_limit", "Too many messages; wait 60 seconds")
    if data.priority == "urgent":
        urgent = sends.where(m.Event.payload["priority"].as_string() == "urgent")
        if db.scalar(select(func.count()).select_from(urgent.subquery())) >= 20:
            fail(429, "urgent_rate_limit", "Urgent-message budget exhausted; wait 60 seconds")
    values = data.model_dump(mode="json", exclude={"deduplicate"})
    fingerprint = digest(json.dumps(values, sort_keys=True, ensure_ascii=False))
    if data.deduplicate:
        previous = db.scalar(
            select(m.Message)
            .where(
                m.Message.project_id == project.id,
                m.Message.sender_id == actor.id,
                m.Message.fingerprint == fingerprint,
                m.Message.created_at > timestamp - timedelta(seconds=60),
            )
            .order_by(m.Message.created_at.desc())
            .limit(1)
        )
        if previous:
            previous.duplicate_count += 1
            previous._event_type = "message.duplicate"
            return previous
    return core.insert(
        db,
        project,
        m.Message,
        sender_id=actor.id,
        fingerprint=fingerprint,
        **data.model_dump(exclude={"deduplicate"}),
    )


def attention(db, project, actor, query):
    timestamp = db.scalar(select(func.clock_timestamp()))
    visible = select(m.Message).where(
        m.Message.project_id == project.id,
        or_(m.Message.recipient_id == actor.id, m.Message.recipient_id.is_(None)),
    )
    # A different human may decide, or the agent may release the task. The original
    # approval request then stops needing attention without inventing a processed receipt.
    automatic_request = and_(
        m.Message.kind == "approval",
        m.Message.group_key.is_not(None),
        m.Message.task_id.is_not(None),
        m.Message.group_key == "task:" + m.Message.task_id + ":approval",
        exists(
            select(m.Task.id).where(
                m.Task.project_id == project.id,
                m.Task.id == m.Message.task_id,
                m.Task.kind == "artifact_review",
            )
        ),
        exists(
            select(m.Actor.id).where(
                m.Actor.project_id == project.id,
                m.Actor.id == m.Message.sender_id,
                m.Actor.kind == "agent",
            )
        ),
    )
    live_request = exists(
        select(m.Task.id).where(
            m.Task.project_id == project.id,
            m.Task.id == m.Message.task_id,
            m.Task.kind == "artifact_review",
            m.Task.status == "running",
            m.Task.approval_status == "pending",
            m.Task.assignee_id == m.Message.sender_id,
            m.Task.lease_until > timestamp,
        )
    )
    visible = visible.where(or_(~automatic_request, live_request))
    # Only explicit status updates supersede one another. A question never disappears
    # because a later message happened to use the same topic or similar text.
    group = case(
        (
            and_(m.Message.kind == "status", m.Message.group_key.is_not(None)),
            "group:" + m.Message.group_key,
        ),
        else_="id:" + m.Message.id,
    )
    ranked = visible.add_columns(
        func.row_number()
        .over(
            partition_by=(
                m.Message.sender_id,
                m.Message.recipient_id,
                m.Message.kind,
                m.Message.task_id,
                group,
            ),
            order_by=(m.Message.created_at.desc(), m.Message.id.desc()),
        )
        .label("latest")
    ).subquery()
    eligible = (
        select(ranked.c.id, ranked.c.priority, ranked.c.created_at)
        .outerjoin(
            m.Receipt,
            and_(
                m.Receipt.project_id == project.id,
                m.Receipt.message_id == ranked.c.id,
                m.Receipt.actor_id == actor.id,
            ),
        )
        .where(
            ranked.c.latest == 1,
            or_(ranked.c.expires_at.is_(None), ranked.c.expires_at > timestamp),
            or_(m.Receipt.stage.is_(None), m.Receipt.stage != "processed"),
        )
        .add_columns(
            func.row_number()
            .over(
                partition_by=(ranked.c.priority, ranked.c.sender_id),
                order_by=(ranked.c.created_at, ranked.c.id),
            )
            .label("sender_turn")
        )
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(eligible))
    queues = []
    for priority in ("urgent", "normal"):
        queues.append(
            list(
                db.scalars(
                    select(m.Message)
                    .join(eligible, eligible.c.id == m.Message.id)
                    .where(eligible.c.priority == priority)
                    .order_by(eligible.c.sender_turn, eligible.c.created_at, eligible.c.id)
                    .limit(query.offset + query.limit)
                )
            )
        )
    ordered, urgent, normal = [], *queues
    ui = ni = 0
    while len(ordered) < query.offset + query.limit and (ui < len(urgent) or ni < len(normal)):
        for _ in range(3):
            if ui < len(urgent):
                ordered.append(urgent[ui])
                ui += 1
        if ni < len(normal):
            ordered.append(normal[ni])
            ni += 1
    result = []
    for message in ordered[query.offset : query.offset + query.limit]:
        receipt = db.get(m.Receipt, (project.id, message.id, actor.id))
        item = {**record(message), "ack_stage": receipt.stage if receipt else None}
        item["body"] = message.body[:2000]
        item["body_truncated"] = len(message.body) > 2000
        item["resource"] = f"messages/{message.id}"
        result.append(item)
    response = {
        "items": result,
        "total": total,
        "has_more": query.offset + query.limit < total,
        "events_after": project.event_seq,
        "server_time": timestamp.isoformat(),
        "policy": "Exact duplicates collapse for 60s; typed status groups keep latest; "
        "expired/processed messages stay in history. Three urgent slots per normal slot; "
        "senders alternate within each priority. Reading does not acknowledge.",
    }
    while (
        response["items"]
        and len(json.dumps(response, ensure_ascii=False, indent=2)) > query.max_chars
    ):
        response["items"].pop()
    response["has_more"] = query.offset + len(response["items"]) < total
    return response
