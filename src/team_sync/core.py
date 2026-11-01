"""All domain operations live here; transports never write tables directly."""

import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from team_sync import models as m
from team_sync import schemas as s
from team_sync.settings import Settings


class DomainError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        self.status, self.code, self.detail = status, code, detail
        super().__init__(detail)


def fail(status, code, detail):
    raise DomainError(status, code, detail)


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def encode(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def record(obj):
    return {
        column.name: encode(getattr(obj, column.name))
        for column in obj.__table__.columns
        if column.name not in {"token_hash", "scope_hash"}
    }


WRITE_ROLES = {"owner", "coordinator", "worker"}
LEAD_ROLES = {"owner", "coordinator"}


class Core:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = create_engine(settings.database_url, pool_pre_ping=True)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def context(self, project_id: str, token: str, *, write=False, roles=None):
        with self.sessions.begin() as db:
            actor = db.scalar(select(m.Actor).where(m.Actor.token_hash == digest(token)))
            if actor is None:
                fail(401, "invalid_credential", "A valid actor credential is required")
            if actor.project_id != project_id:
                fail(404, "not_found", "Project not found")
            query = select(m.Project).where(m.Project.id == project_id)
            # Short shared locks make multi-query reads (snapshot/head/batch) consistent.
            project = db.scalar(query.with_for_update(read=not write))
            db.refresh(actor)
            if not actor.active:
                fail(401, "invalid_credential", "This actor credential has been revoked")
            if roles is not None and actor.role not in roles:
                fail(403, "forbidden", "This role cannot perform this operation")
            yield db, project, actor

    @staticmethod
    def get(db: Session, model, project_id, resource_id):
        obj = db.scalar(
            select(model).where(model.project_id == project_id, model.id == resource_id)
        )
        if obj is None:
            fail(404, "not_found", f"{model.__name__} not found")
        return obj

    @staticmethod
    def emit(db, project, actor, event_type, payload):
        # Every writer holds the project row lock. Sequence order is commit order,
        # unlike a global SQL sequence, which can expose gaps from in-flight writes.
        project.event_seq += 1
        db.add(
            m.Event(
                project_id=project.id,
                seq=project.event_seq,
                type=event_type,
                actor_id=actor.id,
                payload=payload,
            )
        )

    def initialize(self, token: str, data: s.ProjectCreate):
        if not secrets.compare_digest(token, self.settings.bootstrap_token):
            fail(401, "invalid_credential", "A valid bootstrap credential is required")
        credential = secrets.token_urlsafe(32)
        with self.sessions.begin() as db:
            project = m.Project(**data.model_dump(exclude={"owner_name"}))
            db.add(project)
            db.flush()
            actor = m.Actor(
                project_id=project.id,
                name=data.owner_name,
                kind="human",
                role="owner",
                token_hash=digest(credential),
            )
            db.add(actor)
            db.flush()
            self.emit(db, project, actor, "project.created", {"resource_id": project.id})
            db.flush()
            return {"project": record(project), "actor": record(actor), "token": credential}

    def enroll(self, project_id, token, data: s.ActorCreate):
        credential = secrets.token_urlsafe(32)
        with self.context(project_id, token, write=True, roles={"owner"}) as (db, project, actor):
            if data.owner_id:
                owner = self.get(db, m.Actor, project_id, data.owner_id)
                if owner.kind != "human":
                    fail(422, "invalid_owner", "An agent owner must be a human actor")
            new_actor = m.Actor(
                project_id=project_id, token_hash=digest(credential), **data.model_dump()
            )
            db.add(new_actor)
            db.flush()
            self.emit(db, project, actor, "actor.created", {"resource_id": new_actor.id})
            return {"actor": record(new_actor), "token": credential}

    def snapshot(self, project_id, token):
        with self.context(project_id, token) as (db, project, actor):
            return {
                "project": record(project),
                "actor": record(actor),
                "actors": self.rows(db, m.Actor, project_id),
                "challenges": self.rows(db, m.Challenge, project_id),
                "tasks": self.rows(db, m.Task, project_id),
                "events_after": project.event_seq,
                "collections": [
                    "findings",
                    "flags",
                    "verifications",
                    "submissions",
                    "messages",
                    "artifacts",
                ],
            }

    def revoke(self, project_id, token, actor_id):
        with self.context(project_id, token, write=True, roles={"owner"}) as (db, project, actor):
            target = self.get(db, m.Actor, project_id, actor_id)
            if target.active and target.role == "owner":
                owners = db.scalar(
                    select(func.count())
                    .select_from(m.Actor)
                    .where(
                        m.Actor.project_id == project_id,
                        m.Actor.role == "owner",
                        m.Actor.active.is_(True),
                    )
                )
                if owners <= 1:
                    fail(409, "last_owner", "The last active owner cannot be revoked")
            target.active = False
            self.emit(db, project, actor, "actor.revoked", {"resource_id": target.id})
            return record(target)

    @staticmethod
    def rows(db, model, project_id, offset=0, limit=100):
        return [
            record(row)
            for row in db.scalars(
                select(model)
                .where(model.project_id == project_id)
                .order_by(model.created_at, model.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    def listing(self, project_id, token, collection, offset=0, limit=100):
        models = {
            "actors": m.Actor,
            "challenges": m.Challenge,
            "tasks": m.Task,
            "findings": m.Finding,
            "flags": m.Flag,
            "verifications": m.Verification,
            "submissions": m.Submission,
            "artifacts": m.Artifact,
        }
        with self.context(project_id, token) as (db, _, __):
            return self.rows(db, models[collection], project_id, offset, limit)

    def events(self, project_id, token, after=0, limit=100):
        with self.context(project_id, token) as (db, project, _):
            if after > project.event_seq:
                fail(409, "invalid_cursor", "Cursor is beyond the project event sequence")
            rows = db.scalars(
                select(m.Event)
                .where(
                    m.Event.project_id == project_id,
                    m.Event.seq > after,
                )
                .order_by(m.Event.seq)
                .limit(limit)
            )
            items = [record(row) for row in rows]
            return {
                "items": items,
                "next_cursor": items[-1]["seq"] if items else after,
                "head": project.event_seq,
            }

    def inbox(self, project_id, token, offset=0, limit=100):
        with self.context(project_id, token) as (db, _, actor):
            messages = db.scalars(
                select(m.Message)
                .where(
                    m.Message.project_id == project_id,
                    or_(m.Message.recipient_id == actor.id, m.Message.recipient_id.is_(None)),
                )
                .order_by(m.Message.created_at, m.Message.id)
                .offset(offset)
                .limit(limit)
            )
            result = []
            for message in messages:
                receipt = db.get(m.Receipt, (project_id, message.id, actor.id))
                result.append({**record(message), "ack_stage": receipt.stage if receipt else None})
            return result

    def message(self, project_id, token, message_id):
        with self.context(project_id, token) as (db, _, actor):
            message = self.get(db, m.Message, project_id, message_id)
            if message.recipient_id not in (None, actor.id):
                fail(404, "not_found", "Message not found")
            receipt = db.get(m.Receipt, (project_id, message.id, actor.id))
            return {**record(message), "ack_stage": receipt.stage if receipt else None}

    def execute(self, project_id, token, operation, arguments, resource_id, key):
        handlers: dict[str, tuple[type[s.Input], Callable, set]] = {
            "challenge.create": (s.ChallengeCreate, self.create_challenge, WRITE_ROLES),
            "task.create": (s.TaskCreate, self.create_task, WRITE_ROLES),
            "task.claim": (s.Lease, self.claim, WRITE_ROLES),
            "task.renew": (s.Lease, self.renew, WRITE_ROLES),
            "task.complete": (s.Complete, self.complete, WRITE_ROLES),
            "finding.create": (s.FindingCreate, self.create_finding, WRITE_ROLES),
            "flag.create": (s.FlagCreate, self.create_flag, WRITE_ROLES),
            "flag.verify": (s.FlagVerify, self.verify_flag, LEAD_ROLES),
            "submission.create": (s.SubmissionCreate, self.create_submission, WRITE_ROLES),
            "message.send": (s.MessageCreate, self.send_message, WRITE_ROLES),
            "message.ack": (s.MessageAck, self.ack_message, WRITE_ROLES | {"observer"}),
        }
        schema, handler, roles = handlers[operation]
        data = schema.model_validate(arguments)
        fingerprint = digest(
            json.dumps([operation, resource_id, data.model_dump(mode="json")], sort_keys=True)
        )
        with self.context(project_id, token, write=True, roles=roles) as (db, project, actor):
            previous = db.get(m.Command, (project_id, actor.id, key))
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    fail(409, "idempotency_conflict", "Key was already used for another command")
                return previous.response
            result = handler(db, project, actor, data, resource_id)
            db.flush()
            response = record(result)
            payload = {"resource_id": response.get("id", resource_id)}
            for field in ("version", "status", "verification_status", "stage"):
                if field in response:
                    payload[field] = response[field]
            self.emit(db, project, actor, operation, payload)
            db.add(
                m.Command(
                    project_id=project_id,
                    actor_id=actor.id,
                    key=key,
                    fingerprint=fingerprint,
                    response=response,
                )
            )
            return response

    def insert(self, db, project, model, **kwargs):
        item = model(project_id=project.id, **kwargs)
        db.add(item)
        db.flush()
        return item

    def create_challenge(self, db, project, actor, data, resource_id):
        return self.insert(db, project, m.Challenge, **data.model_dump())

    def create_task(self, db, project, actor, data, resource_id):
        if data.challenge_id:
            self.get(db, m.Challenge, project.id, data.challenge_id)
        return self.insert(db, project, m.Task, **data.model_dump(exclude={"category"}))

    @staticmethod
    def check_version(task, expected):
        if task.version != expected:
            fail(409, "version_conflict", f"Current version is {task.version}")

    def claim(self, db, project, actor, data, resource_id):
        task = self.get(db, m.Task, project.id, resource_id)
        self.check_version(task, data.expected_version)
        timestamp = db.scalar(select(func.clock_timestamp()))
        if task.status == "done" or (task.lease_until and task.lease_until > timestamp):
            fail(409, "task_unavailable", "Task is completed or has a live lease")
        task.assignee_id, task.status = actor.id, "running"
        task.lease_until = timestamp + timedelta(seconds=data.lease_seconds)
        task.version += 1
        return task

    def owned_task(self, db, project, actor, data, resource_id):
        task = self.get(db, m.Task, project.id, resource_id)
        self.check_version(task, data.expected_version)
        timestamp = db.scalar(select(func.clock_timestamp()))
        if (
            task.status != "running"
            or task.assignee_id != actor.id
            or task.lease_until is None
            or task.lease_until <= timestamp
        ):
            fail(409, "lease_invalid", "A live lease owned by this actor is required")
        return task, timestamp

    def renew(self, db, project, actor, data, resource_id):
        task, timestamp = self.owned_task(db, project, actor, data, resource_id)
        task.lease_until = timestamp + timedelta(seconds=data.lease_seconds)
        task.version += 1
        return task

    def complete(self, db, project, actor, data, resource_id):
        task, _ = self.owned_task(db, project, actor, data, resource_id)
        task.status, task.result, task.lease_until = "done", data.result, None
        task.version += 1
        return task

    def create_finding(self, db, project, actor, data, resource_id):
        if data.challenge_id:
            self.get(db, m.Challenge, project.id, data.challenge_id)
        if data.task_id:
            task = self.get(db, m.Task, project.id, data.task_id)
            if data.challenge_id and task.challenge_id != data.challenge_id:
                fail(422, "scope_mismatch", "Finding challenge does not match its task")
        return self.insert(db, project, m.Finding, author_id=actor.id, **data.model_dump())

    def create_flag(self, db, project, actor, data, resource_id):
        if not project.capabilities.get("flags", False):
            fail(409, "capability_disabled", "Flags are disabled for this project")
        if data.challenge_id:
            self.get(db, m.Challenge, project.id, data.challenge_id)
        scope_hash = digest(
            json.dumps([data.value, data.challenge_id, data.target, data.round], ensure_ascii=False)
        )
        existing = db.scalar(
            select(m.Flag).where(m.Flag.project_id == project.id, m.Flag.scope_hash == scope_hash)
        )
        if existing:
            fail(409, "duplicate_flag", f"Flag already recorded as {existing.id}")
        return self.insert(
            db, project, m.Flag, author_id=actor.id, scope_hash=scope_hash, **data.model_dump()
        )

    def verify_flag(self, db, project, actor, data, resource_id):
        flag = self.get(db, m.Flag, project.id, resource_id)
        self.check_version(flag, data.expected_version)
        verification = self.insert(
            db,
            project,
            m.Verification,
            flag_id=flag.id,
            author_id=actor.id,
            status=data.status,
            method=data.method,
            evidence=data.evidence,
        )
        flag.verification_status = data.status
        flag.verification = {
            "id": verification.id,
            "method": data.method,
            "evidence": data.evidence,
            "actor_id": actor.id,
            "at": db.scalar(select(func.clock_timestamp())).isoformat(),
        }
        flag.version += 1
        return flag

    def create_submission(self, db, project, actor, data, resource_id):
        self.get(db, m.Flag, project.id, resource_id)
        # This records a reported outcome; it never sends flags to a CTF platform.
        return self.insert(
            db,
            project,
            m.Submission,
            flag_id=resource_id,
            author_id=actor.id,
            **data.model_dump(),
        )

    def send_message(self, db, project, actor, data, resource_id):
        if data.recipient_id:
            self.get(db, m.Actor, project.id, data.recipient_id)
        return self.insert(db, project, m.Message, sender_id=actor.id, **data.model_dump())

    def ack_message(self, db, project, actor, data, resource_id):
        message = self.get(db, m.Message, project.id, resource_id)
        if message.recipient_id not in (None, actor.id):
            fail(404, "not_found", "Message not found")
        receipt = db.get(m.Receipt, (project.id, message.id, actor.id))
        if receipt is None:
            receipt = m.Receipt(
                project_id=project.id, message_id=message.id, actor_id=actor.id, stage=data.stage
            )
            db.add(receipt)
        elif receipt.stage == "processed" and data.stage == "received":
            fail(409, "receipt_regression", "A processed receipt cannot move backwards")
        else:
            receipt.stage, receipt.updated_at = data.stage, m.now()
        return receipt

    def add_artifact(self, project_id, token, name, sha256, size, key):
        with self.context(project_id, token, write=True, roles=WRITE_ROLES) as (db, project, actor):
            fingerprint = digest(json.dumps(["artifact.publish", name, sha256, size]))
            previous = db.get(m.Command, (project_id, actor.id, key))
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    fail(409, "idempotency_conflict", "Key was already used for another command")
                return previous.response
            artifact = self.insert(
                db, project, m.Artifact, name=name, sha256=sha256, size=size, author_id=actor.id
            )
            self.emit(db, project, actor, "artifact.published", {"resource_id": artifact.id})
            response = record(artifact)
            db.add(
                m.Command(
                    project_id=project_id,
                    actor_id=actor.id,
                    key=key,
                    fingerprint=fingerprint,
                    response=response,
                )
            )
            return response

    def artifact(self, project_id, token, artifact_id):
        with self.context(project_id, token) as (db, _, __):
            return record(self.get(db, m.Artifact, project_id, artifact_id))
