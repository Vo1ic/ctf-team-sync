"""All domain operations live here; transports never write tables directly."""

import json
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from team_sync import models as m
from team_sync import schemas as s
from team_sync.domain import DomainError as DomainError
from team_sync.domain import digest, fail, record
from team_sync.editing import EditingOperations, flag_subject, receipt_stage
from team_sync.fleet import FleetOperations
from team_sync.graph import GraphOperations
from team_sync.journal import JournalOperations
from team_sync.onboarding import OnboardingOperations
from team_sync.review import ReviewOperations
from team_sync.settings import Settings
from team_sync.workflows import WorkflowOperations, actor_records

WRITE_ROLES = {"owner", "coordinator", "worker"}
LEAD_ROLES = {"owner", "coordinator"}


class Core(
    WorkflowOperations,
    EditingOperations,
    JournalOperations,
    OnboardingOperations,
    GraphOperations,
    ReviewOperations,
    FleetOperations,
):
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
        from team_sync.onboarding import render_briefing

        credential = secrets.token_urlsafe(32)
        with self.context(project_id, token, write=True, roles=WRITE_ROLES) as (db, project, actor):
            owner_id = self.enroll_permission(db, project, actor, data)
            if owner_id:
                owner = self.get(db, m.Actor, project_id, owner_id)
                if owner.kind != "human":
                    fail(422, "invalid_owner", "An agent owner must be a human actor")
            new_actor = m.Actor(
                project_id=project_id,
                token_hash=digest(credential),
                name=self.name_for(db, project_id, data.kind, data.name, owner_id),
                **data.model_dump(exclude={"name", "owner_id"}),
                owner_id=owner_id,
            )
            db.add(new_actor)
            db.flush()
            self.emit(db, project, actor, "actor.created", {"resource_id": new_actor.id})
            return {
                "actor": record(new_actor),
                "token": credential,
                "briefing": render_briefing(project),
            }

    def snapshot(self, project_id, token):
        with self.context(project_id, token) as (db, project, actor):
            return {
                "project": record(project),
                "actor": actor_records(db, [actor])[0],
                "server_time": db.scalar(select(func.clock_timestamp())).isoformat(),
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

    def agent_context(self, project_id, token, query):
        from team_sync.workbench import workspace

        with self.context(project_id, token) as (db, project, actor):
            return workspace(self, db, project, actor, query)

    def resource(self, project_id, token, collection, resource_id, offset=0, limit=10):
        from team_sync.workbench import resource

        with self.context(project_id, token) as (db, project, actor):
            return resource(self, db, project, actor, collection, resource_id, offset, limit)

    def dossier(self, project_id, token, challenge_id):
        from team_sync.dossier import build

        with self.context(project_id, token) as (db, project, _):
            return build(db, project, self.get(db, m.Challenge, project_id, challenge_id))

    def search(self, project_id, token, query):
        from team_sync.workbench import search

        with self.context(project_id, token) as (db, project, actor):
            return search(db, project, actor, query)

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
            before = record(target)
            target.active = False
            target.version += 1
            self._revision(db, project, actor, "actors", target, before, "Access revoked")
            self.emit(db, project, actor, "actor.revoked", {"resource_id": target.id})
            return record(target)

    @staticmethod
    def rows(db, model, project_id, offset=0, limit=100):
        rows = list(
            db.scalars(
                select(model)
                .where(model.project_id == project_id)
                .order_by(model.created_at, model.id)
                .offset(offset)
                .limit(limit)
            )
        )
        return actor_records(db, rows) if model is m.Actor else [record(row) for row in rows]

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
            "activities": m.Activity,
            "logs": m.Log,
            "links": m.Link,
            "hint_requests": m.HintRequest,
            "runners": m.Runner,
            "runs": m.Run,
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
                "server_time": db.scalar(select(func.clock_timestamp())).isoformat(),
            }

    def inbox(self, project_id, token, offset=0, limit=100):
        with self.context(project_id, token) as (db, _, actor):
            messages = db.scalars(
                select(m.Message)
                .where(
                    m.Message.project_id == project_id,
                    or_(m.Message.recipient_id == actor.id, m.Message.recipient_id.is_(None)),
                    # Your own broadcasts live in the outbox, not in your inbox.
                    m.Message.sender_id != actor.id,
                )
                .order_by(m.Message.created_at, m.Message.id)
                .offset(offset)
                .limit(limit)
            )
            result = []
            for message in messages:
                receipt = db.get(m.Receipt, (project_id, message.id, actor.id))
                result.append({**record(message), "ack_stage": receipt_stage(receipt, message)})
            return result

    def message(self, project_id, token, message_id):
        with self.context(project_id, token) as (db, _, actor):
            message = self.get(db, m.Message, project_id, message_id)
            if message.recipient_id not in (None, actor.id):
                fail(404, "not_found", "Message not found")
            receipt = db.get(m.Receipt, (project_id, message.id, actor.id))
            return {**record(message), "ack_stage": receipt_stage(receipt, message)}

    def outbox(self, project_id, token, offset=0, limit=100):
        from team_sync.workbench import sent_messages

        with self.context(project_id, token) as (db, project, actor):
            rows = db.execute(
                sent_messages(project, actor)
                .order_by(m.Message.created_at, m.Message.id)
                .offset(offset)
                .limit(limit)
            )
            return [{**record(message), "recipient_ack_stage": stage} for message, stage in rows]

    def execute(self, project_id, token, operation, arguments, resource_id, key):
        handlers: dict[str, tuple[type[s.Input], Callable, set]] = {
            "resource.update": (s.ResourceEdit, self.edit_resource, WRITE_ROLES | {"observer"}),
            "log.create": (s.LogCreate, self.create_log, WRITE_ROLES),
            "challenge.create": (s.ChallengeCreate, self.create_challenge, WRITE_ROLES),
            "task.create": (s.TaskCreate, self.create_task, WRITE_ROLES),
            "task.claim": (s.Lease, self.claim, WRITE_ROLES),
            "task.renew": (s.Lease, self.renew, WRITE_ROLES),
            "task.complete": (s.Complete, self.complete, WRITE_ROLES),
            "task.release": (s.Release, self.release, WRITE_ROLES),
            "task.assign": (s.Assign, self.assign_task, WRITE_ROLES),
            "task.join": (s.TaskJoin, self.join_task, WRITE_ROLES),
            "task.leave": (s.TaskLeave, self.leave_task, WRITE_ROLES),
            "task.join_many": (s.TaskJoinMany, self.join_many, WRITE_ROLES),
            "link.create": (s.LinkCreate, self.create_link, WRITE_ROLES),
            "link.delete": (s.LinkDelete, self.delete_link, WRITE_ROLES),
            "hint.request": (s.HintRequestCreate, self.request_hint, WRITE_ROLES),
            "hint.decide": (s.HintDecision, self.decide_hint, WRITE_ROLES),
            "hint.used": (s.HintUsed, self.hint_used, WRITE_ROLES),
            "question.ask": (s.QuestionAsk, self.ask_question, WRITE_ROLES),
            "question.answer": (s.QuestionAnswer, self.answer_question, WRITE_ROLES),
            "run.request": (s.RunRequest, self.request_run, WRITE_ROLES),
            "run.decide": (s.RunDecision, self.decide_run, WRITE_ROLES),
            "run.cancel": (s.RunCancel, self.cancel_run, WRITE_ROLES),
            "actor.rename": (s.Rename, self.rename_actor, WRITE_ROLES | {"observer"}),
            "activity.report": (s.ActivityReport, self.report_activity, WRITE_ROLES | {"observer"}),
            "artifact.review.request": (s.ReviewRequest, self.review_request, WRITE_ROLES),
            "artifact.review.decide": (s.ReviewDecision, self.review_decide, WRITE_ROLES),
            "artifact.review.start": (s.Versioned, self.review_start, WRITE_ROLES),
            "artifact.review.complete": (s.ReviewFinish, self.review_complete, WRITE_ROLES),
            "artifact.archive": (s.Archive, self.archive_artifact, WRITE_ROLES),
            "finding.create": (s.FindingCreate, self.create_finding, WRITE_ROLES),
            "flag.create": (s.FlagCreate, self.create_flag, WRITE_ROLES),
            "flag.verify": (s.FlagVerify, self.verify_flag, LEAD_ROLES),
            "submission.create": (s.SubmissionCreate, self.create_submission, WRITE_ROLES),
            "message.send": (s.MessageCreate, self.send_message, WRITE_ROLES),
            "message.ack": (s.MessageAck, self.ack_message, WRITE_ROLES | {"observer"}),
        }
        schema, handler, roles = handlers[operation]
        data = schema.model_validate(arguments)
        fingerprint_data = data.model_dump(mode="json")
        # Preserve command hashes for in-flight v0.2 calls when new fields use defaults.
        defaults = {
            "task.create": {
                "requested_assignee_id": None,
                "priority": "normal",
                "due_at": None,
                "points": None,
                "source_url": None,
                "category": None,
                "allow_duplicate": False,
            },
            "flag.create": {"task_id": None},
            "task.complete": {"without_flag": False},
            "flag.verify": {"method": "manual", "evidence": ""},
            "challenge.create": {"points": None, "source_url": None, "allow_duplicate": False},
            "resource.update": {"force": False},
            "finding.create": {"artifact_id": None, "status": None, "based_on": [], "refutes": []},
            "message.ack": {"expected_version": None},
            "message.send": {
                "kind": "note",
                "priority": "normal",
                "task_id": None,
                "group_key": None,
                "expires_at": None,
                "reply_to_id": None,
                "deduplicate": True,
            },
        }.get(operation, {})
        for field, default in defaults.items():
            if fingerprint_data.get(field) == default:
                fingerprint_data.pop(field, None)
        fingerprint = digest(json.dumps([operation, resource_id, fingerprint_data], sort_keys=True))
        with self.context(project_id, token, write=True, roles=roles) as (db, project, actor):
            previous = db.get(m.Command, (project_id, actor.id, key))
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    fail(409, "idempotency_conflict", "Key was already used for another command")
                return previous.response
            result = handler(db, project, actor, data, resource_id)
            db.flush()
            if isinstance(result, dict):
                # Bulk operations return a summary instead of one row.
                response = {k: v for k, v in result.items() if not k.startswith("_")}
                ids = response.get("joined") or response.get("already_joined") or []
                payload = {
                    "resource_id": ids[0] if ids else resource_id,
                    "resource_type": response.get("resource_type"),
                    **{k: response[k] for k in ("actor_id", "category") if response.get(k)},
                    "count": len(response.get("joined", [])),
                }
                self.emit(db, project, actor, result.get("_event_type", operation), payload)
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
            response = record(result)
            payload = {"resource_id": response.get("id", resource_id)}
            for field in (
                "version",
                "status",
                "verification_status",
                "stage",
                "priority",
                "kind",
                "approval_status",
                "message_version",
                "level",
            ):
                if field in response:
                    payload[field] = response[field]
            payload["resource_type"] = result.__tablename__
            if isinstance(result, m.Task) and result.artifact_id:
                payload["artifact_id"] = result.artifact_id
            if hasattr(result, "_revision_id"):
                payload["revision_id"] = result._revision_id
            payload.update(getattr(result, "_event_extra", {}))
            if operation != "activity.report" and not getattr(result, "_skip_command_event", False):
                self.emit(db, project, actor, getattr(result, "_event_type", operation), payload)
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
        if not data.allow_duplicate:
            existing = db.scalar(
                select(m.Challenge.id).where(
                    m.Challenge.project_id == project.id,
                    func.lower(func.trim(m.Challenge.title)) == data.title.strip().casefold(),
                )
            )
            if existing:
                fail(
                    409,
                    "duplicate_challenge",
                    f"A challenge with this title already exists: {existing}",
                    existing_id=existing,
                )
        return self.insert(db, project, m.Challenge, **data.model_dump(exclude={"allow_duplicate"}))

    def create_task(self, db, project, actor, data, resource_id):
        if data.challenge_id:
            self.get(db, m.Challenge, project.id, data.challenge_id)
        if not data.allow_duplicate:
            existing = db.scalar(
                select(m.Task.id).where(
                    m.Task.project_id == project.id,
                    m.Task.status != "done",
                    func.lower(func.trim(m.Task.title)) == data.title.strip().casefold(),
                    (m.Task.challenge_id == data.challenge_id)
                    if data.challenge_id
                    else m.Task.challenge_id.is_(None),
                )
            )
            if existing:
                fail(
                    409,
                    "duplicate_task",
                    f"An unfinished task with this title already exists: {existing}",
                    existing_id=existing,
                )
        target = (
            self.get(db, m.Actor, project.id, data.requested_assignee_id)
            if data.requested_assignee_id
            else None
        )
        if target and (not target.active or target.role == "observer"):
            fail(409, "inactive_recipient", "Select an active participant with permission to work")
        task = self.insert(
            db,
            project,
            m.Task,
            created_by_id=actor.id,
            **data.model_dump(exclude={"category", "points", "source_url", "allow_duplicate"}),
        )
        if target and target.id != actor.id:
            self._notice(
                db,
                project,
                actor,
                target.id,
                f"{actor.name} доручив роботу: {task.title}",
                task,
                "assignment",
            )
        return task

    @staticmethod
    def check_version(task, expected):
        if task.version != expected:
            fail(
                409,
                "version_conflict",
                f"Current version is {task.version}",
                current_version=task.version,
                resource_id=task.id,
            )

    def claim(self, db, project, actor, data, resource_id):
        task = self.get(db, m.Task, project.id, resource_id)
        self.check_version(task, data.expected_version)
        timestamp = db.scalar(select(func.clock_timestamp()))
        if task.status in {"done", "review"} or (task.lease_until and task.lease_until > timestamp):
            fail(
                409,
                "task_unavailable",
                "Task is completed, waits for flag confirmation, or has a live lease",
            )
        if task.requested_assignee_id not in (None, actor.id):
            fail(403, "task_reserved", "This task is addressed to another participant")
        previous_assignee = task.assignee_id
        if task.kind == "artifact_review":
            artifact = self.get(db, m.Artifact, project.id, task.artifact_id)
            if actor.kind != "agent" or artifact.archived:
                fail(
                    409,
                    "review_unavailable",
                    "An active artifact review must be claimed by an agent",
                )
            if previous_assignee != actor.id:
                task.approval_status, task.approved_by_id, task.analysis_started_at = (
                    "pending",
                    None,
                    None,
                )
        task.assignee_id, task.status = actor.id, "running"
        task.lease_until = timestamp + timedelta(seconds=data.lease_seconds)
        task.version += 1
        if task.kind == "artifact_review" and task.approval_status == "pending":
            self._approval_request(db, project, actor, task)
            self._activity(
                db, project, actor, actor, "waiting_human", "Очікує дозволу на аналіз", task
            )
        else:
            self._activity(db, project, actor, actor, self.task_activity(task), task.title, task)
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
        self._activity(
            db,
            project,
            actor,
            actor,
            self.task_activity(task),
            task.title,
            task,
        )
        return task

    def complete(self, db, project, actor, data, resource_id):
        task, _ = self.owned_task(db, project, actor, data, resource_id)
        if task.kind == "artifact_review":
            fail(
                409,
                "review_report_required",
                "Complete artifact analysis with a README and verdict",
            )
        task.result, task.lease_until = data.result, None
        if self.needs_flag(project, task, data.without_flag) and not self.confirmed_flag(
            db, project.id, task.challenge_id
        ):
            # Not solved until a human confirms a correct flag for this challenge.
            task.status = "review"
            self.request_flag_review(db, project, actor, task)
        else:
            task.status = "done"
        task.version += 1
        self._after_task(db, project, actor)
        return task

    def release(self, db, project, actor, data, resource_id):
        task, _ = self.owned_task(db, project, actor, data, resource_id)
        finding = self.insert(
            db,
            project,
            m.Finding,
            author_id=actor.id,
            title=f"Handoff: {task.title}"[:200],
            body=data.reason,
            evidence="The assignee explicitly released a live task lease.",
            kind="observation",
            task_id=task.id,
            challenge_id=task.challenge_id,
            artifact_id=task.artifact_id,
        )
        self.emit(db, project, actor, "finding.create", {"resource_id": finding.id})
        task.status, task.assignee_id, task.lease_until = "open", None, None
        task.requested_assignee_id = None
        if task.kind == "artifact_review":
            task.approval_status, task.approved_by_id, task.analysis_started_at = (
                "pending",
                None,
                None,
            )
        task.version += 1
        self._after_task(db, project, actor)
        return task

    def create_finding(self, db, project, actor, data, resource_id):
        if data.challenge_id:
            self.get(db, m.Challenge, project.id, data.challenge_id)
        if data.task_id:
            task = self.get(db, m.Task, project.id, data.task_id)
            if data.challenge_id and task.challenge_id != data.challenge_id:
                fail(422, "scope_mismatch", "Finding challenge does not match its task")
            # Group task findings under their challenge for search and the web UI.
            data = data.model_copy(update={"challenge_id": task.challenge_id})
        if data.artifact_id:
            self.get(db, m.Artifact, project.id, data.artifact_id)
        status = data.status or ("open" if data.kind == "hypothesis" else None)
        finding = self.insert(
            db,
            project,
            m.Finding,
            author_id=actor.id,
            status=status,
            **data.model_dump(exclude={"status", "based_on", "refutes"}),
        )
        self.chain_finding(db, project, actor, finding, data.based_on, data.refutes)
        return finding

    def create_flag(self, db, project, actor, data, resource_id):
        if not project.capabilities.get("flags", False):
            fail(409, "capability_disabled", "Flags are disabled for this project")
        if data.task_id:
            task = self.get(db, m.Task, project.id, data.task_id)
            if data.challenge_id and task.challenge_id and task.challenge_id != data.challenge_id:
                fail(422, "scope_mismatch", "Flag challenge does not match its task")
            data = data.model_copy(update={"challenge_id": data.challenge_id or task.challenge_id})
        if data.challenge_id:
            self.get(db, m.Challenge, project.id, data.challenge_id)
        scope_hash = digest(
            json.dumps([data.value, data.challenge_id, data.target, data.round], ensure_ascii=False)
        )
        existing = db.scalar(
            select(m.Flag).where(m.Flag.project_id == project.id, m.Flag.scope_hash == scope_hash)
        )
        if existing:
            fail(
                409,
                "duplicate_flag",
                f"Flag already recorded as {existing.id}",
                existing_id=existing.id,
            )
        return self.insert(
            db, project, m.Flag, author_id=actor.id, scope_hash=scope_hash, **data.model_dump()
        )

    def verify_flag(self, db, project, actor, data, resource_id):
        if actor.kind != "human":
            # Coordinator agents organise work; only a person decides a flag is correct.
            fail(403, "human_verification_required", "Only a human confirms or rejects flags")
        flag = self.get(db, m.Flag, project.id, resource_id)
        self.check_version(flag, data.expected_version)
        if not data.evidence.strip():
            label = "Підтверджено" if data.status == "confirmed" else "Спростовано"
            data = data.model_copy(update={"evidence": f"{label} без коментаря ({actor.name})"})
        verification = self.insert(
            db,
            project,
            m.Verification,
            flag_id=flag.id,
            author_id=actor.id,
            subject=flag_subject(flag),
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
        self.settle_reviews(db, project, actor, flag)
        return flag

    def create_submission(self, db, project, actor, data, resource_id):
        flag = self.get(db, m.Flag, project.id, resource_id)
        # This records a reported outcome; it never sends flags to a CTF platform.
        return self.insert(
            db,
            project,
            m.Submission,
            flag_id=resource_id,
            author_id=actor.id,
            subject=flag_subject(flag),
            **data.model_dump(),
        )

    def send_message(self, db, project, actor, data, resource_id):
        if data.recipient_id:
            self.get(db, m.Actor, project.id, data.recipient_id)
        from team_sync.attention import create_message

        return create_message(self, db, project, actor, data)

    def ack_message(self, db, project, actor, data, resource_id):
        message = self.get(db, m.Message, project.id, resource_id)
        if message.recipient_id not in (None, actor.id):
            fail(404, "not_found", "Message not found")
        if data.expected_version is not None:
            self.check_version(message, data.expected_version)
        elif message.version > 1:
            fail(
                409,
                "message_version_required",
                "Read the edited message and acknowledge its version",
            )
        receipt = db.get(m.Receipt, (project.id, message.id, actor.id))
        if receipt is None:
            receipt = m.Receipt(
                project_id=project.id,
                message_id=message.id,
                actor_id=actor.id,
                stage=data.stage,
                message_version=message.version,
            )
            db.add(receipt)
        elif (
            receipt.message_version == message.version
            and receipt.stage == "processed"
            and data.stage == "received"
        ):
            fail(409, "receipt_regression", "A processed receipt cannot move backwards")
        else:
            receipt.stage, receipt.updated_at = data.stage, m.now()
            receipt.message_version = message.version
        return receipt

    def add_artifact(
        self,
        project_id,
        token,
        name,
        sha256,
        size,
        key,
        requires_review=False,
        media_type="application/octet-stream",
        supersedes_id=None,
        expected_version=None,
    ):
        with self.context(project_id, token, write=True, roles=WRITE_ROLES) as (db, project, actor):
            if expected_version is not None and supersedes_id is None:
                fail(
                    422, "artifact_source_required", "Choose the previous artifact for this version"
                )
            fields = ["artifact.publish", name, sha256, size]
            if requires_review or media_type != "application/octet-stream":
                fields += [{"requires_review": requires_review, "media_type": media_type}]
            if supersedes_id:
                fields += [{"supersedes_id": supersedes_id, "expected_version": expected_version}]
            fingerprint = digest(json.dumps(fields))
            previous = db.get(m.Command, (project_id, actor.id, key))
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    fail(409, "idempotency_conflict", "Key was already used for another command")
                return previous.response
            source = None
            if supersedes_id:
                source = self.get(db, m.Artifact, project_id, supersedes_id)
                self.edit_permission(actor, source, "artifacts", {})
                if expected_version is None:
                    fail(422, "version_required", "The previous artifact version is required")
                self.check_version(source, expected_version)
                successor = db.scalar(
                    select(m.Artifact.id).where(
                        m.Artifact.project_id == project_id, m.Artifact.supersedes_id == source.id
                    )
                )
                if successor:
                    fail(
                        409, "artifact_superseded", f"Edit the latest artifact instead: {successor}"
                    )
                requires_review = requires_review or source.requires_review
            artifact = self.insert(
                db,
                project,
                m.Artifact,
                name=name,
                sha256=sha256,
                size=size,
                author_id=actor.id,
                requires_review=requires_review,
                media_type=media_type,
                supersedes_id=supersedes_id,
            )
            if source:
                before = record(source)
                source.version += 1
                self._revision(
                    db,
                    project,
                    actor,
                    "artifacts",
                    source,
                    before,
                    f"New immutable content version: artifact:{artifact.id}",
                )
                self.emit(
                    db,
                    project,
                    actor,
                    "artifact.update",
                    {
                        "resource_id": source.id,
                        "version": source.version,
                        "successor_id": artifact.id,
                    },
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
        with self.context(project_id, token) as (db, _, actor):
            artifact = self.get(db, m.Artifact, project_id, artifact_id)
            if actor.kind == "agent":
                if artifact.archived:
                    fail(410, "artifact_archived", "The artifact has been archived")
                if artifact.requires_review:
                    timestamp = db.scalar(select(func.clock_timestamp()))
                    approved = db.scalar(
                        select(m.Task.id)
                        .where(
                            m.Task.project_id == project_id,
                            m.Task.artifact_id == artifact_id,
                            m.Task.kind == "artifact_review",
                            m.Task.approval_status == "approved",
                            m.Task.assignee_id == actor.id,
                            m.Task.status == "running",
                            m.Task.lease_until > timestamp,
                            m.Task.analysis_started_at.is_not(None),
                        )
                        .limit(1)
                    )
                    if not approved:
                        fail(
                            403,
                            "analysis_not_approved",
                            "Claim review, obtain human approval, then start before downloading",
                        )
            return record(artifact)

    def attention(self, project_id, token, query):
        from team_sync.attention import attention

        with self.context(project_id, token) as (db, project, actor):
            return attention(db, project, actor, query)
