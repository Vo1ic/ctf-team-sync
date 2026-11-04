"""Bounded activity journal over the existing event order and explicit agent logs."""

import json
from datetime import timedelta

from sqlalchemy import and_, case, exists, func, or_, select

from team_sync import models as m
from team_sync.domain import fail, record
from team_sync.editing import MODELS

PREFIXES = {
    "project": "projects",
    "actor": "actors",
    "challenge": "challenges",
    "task": "tasks",
    "finding": "findings",
    "flag": "flags",
    "verification": "verifications",
    "submission": "submissions",
    "message": "messages",
    "artifact": "artifacts",
    "activity": "activities",
    "log": "logs",
}
ROUTINE = {"activity.update", "activity.report", "task.renew", "message.ack", "message.duplicate"}


def event_collection():
    return case(
        (m.Event.type.like("artifact.review.%"), "tasks"),
        *(
            (m.Event.type.like(prefix + ".%"), collection)
            for prefix, collection in PREFIXES.items()
        ),
        else_="projects",
    )


class JournalOperations:
    def create_log(self, db, project, actor, data, resource_id):
        timestamp = db.scalar(select(func.clock_timestamp()))
        count = db.scalar(
            select(func.count())
            .select_from(m.Event)
            .where(
                m.Event.project_id == project.id,
                m.Event.actor_id == actor.id,
                m.Event.type == "log.create",
                m.Event.created_at > timestamp - timedelta(seconds=60),
            )
        )
        if count >= 120:
            fail(429, "log_rate_limit", "Too many log entries; wait 60 seconds")
        challenge_id = data.challenge_id
        if data.task_id:
            task = self.get(db, m.Task, project.id, data.task_id)
            if challenge_id and task.challenge_id != challenge_id:
                fail(422, "scope_mismatch", "Log challenge must match its task")
            challenge_id = challenge_id or task.challenge_id
        if challenge_id:
            self.get(db, m.Challenge, project.id, challenge_id)
        return self.insert(
            db,
            project,
            m.Log,
            author_id=actor.id,
            **{**data.model_dump(), "challenge_id": challenge_id},
        )

    def journal(self, project_id, token, query):
        with self.context(project_id, token) as (db, project, actor):
            if query.actor_id:
                self.get(db, m.Actor, project_id, query.actor_id)
            collection = event_collection()
            stmt = select(m.Event).where(m.Event.project_id == project_id)
            if query.before:
                stmt = stmt.where(m.Event.seq < query.before)
            if query.actor_id:
                stmt = stmt.where(m.Event.actor_id == query.actor_id)
            if query.resource_id:
                stmt = stmt.where(
                    or_(
                        m.Event.payload["resource_id"].as_string() == query.resource_id,
                        m.Event.payload["artifact_id"].as_string() == query.resource_id,
                        and_(
                            m.Event.type == "log.create",
                            exists(
                                select(m.Log.id).where(
                                    m.Log.project_id == project_id,
                                    m.Log.id == m.Event.payload["resource_id"].as_string(),
                                    or_(
                                        m.Log.task_id == query.resource_id,
                                        m.Log.challenge_id == query.resource_id,
                                    ),
                                )
                            ),
                        ),
                    )
                )
            if query.collection:
                stmt = stmt.where(collection == query.collection)
            if query.event_type:
                stmt = stmt.where(m.Event.type == query.event_type)
            if not query.include_routine and not query.event_type:
                stmt = stmt.where(m.Event.type.not_in(ROUTINE))
            if query.level:
                stmt = stmt.where(
                    m.Event.type == "log.create",
                    exists(
                        select(m.Log.id).where(
                            m.Log.project_id == project_id,
                            m.Log.id == m.Event.payload["resource_id"].as_string(),
                            m.Log.level == query.level,
                        )
                    ),
                )
            if query.query:
                escaped = query.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                text = f"%{escaped}%"
                stmt = stmt.where(
                    or_(
                        m.Event.type.ilike(text, escape="\\"),
                        exists(
                            select(m.Actor.id).where(
                                m.Actor.project_id == project_id,
                                m.Actor.id == m.Event.actor_id,
                                m.Actor.name.ilike(text, escape="\\"),
                            )
                        ),
                        and_(
                            m.Event.type == "log.create",
                            exists(
                                select(m.Log.id).where(
                                    m.Log.project_id == project_id,
                                    m.Log.id == m.Event.payload["resource_id"].as_string(),
                                    m.Log.body.ilike(text, escape="\\"),
                                )
                            ),
                        ),
                    )
                )
            rows = list(db.scalars(stmt.order_by(m.Event.seq.desc()).limit(query.limit + 1)))
            authors = {
                entry.id: entry
                for entry in db.scalars(
                    select(m.Actor).where(
                        m.Actor.project_id == project_id,
                        m.Actor.id.in_([row.actor_id for row in rows]),
                    )
                )
            }
            groups = {}
            for row in rows:
                name = self._event_collection(row.type)
                ident = row.payload.get("resource_id")
                if ident and name != "projects":
                    groups.setdefault(name, set()).add(ident)
            resources = {}
            for name, ids in groups.items():
                model = MODELS[name]
                for item in db.scalars(
                    select(model).where(model.project_id == project_id, model.id.in_(ids))
                ):
                    resources[(name, item.id)] = item
            result = {
                "items": [],
                "has_more": False,
                "next_before": None,
                "head": project.event_seq,
                "server_time": db.scalar(select(func.clock_timestamp())).isoformat(),
                "filters": query.model_dump(mode="json", exclude_none=True),
            }
            for event in rows[: query.limit]:
                author = authors.get(event.actor_id)
                name = self._event_collection(event.type)
                ident = event.payload.get("resource_id")
                item = (
                    project
                    if name == "projects" and ident == project.id
                    else resources.get((name, ident))
                )
                label = getattr(item, "title", None) or getattr(item, "name", None)
                if name == "messages":
                    # Audit remains metadata-only, including for owners outside this conversation.
                    label = "Повідомлення"
                elif name == "logs":
                    label = "Запис журналу"
                elif name == "flags":
                    label = "Флаг"
                entry = {
                    "id": event.id,
                    "seq": event.seq,
                    "type": event.type,
                    "created_at": event.created_at.isoformat(),
                    "actor": {
                        "id": event.actor_id,
                        "name": author.name if author else "Учасник",
                        "kind": author.kind if author else None,
                    },
                    "resource": {"collection": name, "id": ident, "label": (label or name)[:180]},
                    "revision_id": event.payload.get("revision_id"),
                    "payload": event.payload,
                }
                entry["resource"]["readable"] = item is not None and (
                    not isinstance(item, m.Message)
                    or item.recipient_id is None
                    or actor.id in {item.sender_id, item.recipient_id}
                )
                if isinstance(item, m.Log):
                    entry["log"] = {
                        **record(item),
                        "body": item.body[:1500],
                        "body_truncated": len(item.body) > 1500,
                    }
                candidate = result["items"] + [entry]
                if (
                    len(json.dumps({**result, "items": candidate}, ensure_ascii=False, indent=2))
                    > query.max_chars
                ):
                    if result["items"]:
                        break
                    if "log" in entry:
                        entry["log"]["body"] = item.body[:300]
                        entry["log"]["body_truncated"] = len(item.body) > 300
                result["items"].append(entry)
            result["has_more"] = len(rows) > len(result["items"])
            result["next_before"] = result["items"][-1]["seq"] if result["items"] else None
            while (
                len(result["items"]) > 1
                and len(json.dumps(result, ensure_ascii=False, indent=2)) > query.max_chars
            ):
                result["items"].pop()
                result["has_more"] = True
                result["next_before"] = result["items"][-1]["seq"]
            return result

    @staticmethod
    def _event_collection(event_type):
        if event_type.startswith("artifact.review."):
            return "tasks"
        return PREFIXES.get(event_type.split(".")[0], "projects")
