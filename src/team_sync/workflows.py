"""Small workflow operations called inside Core's serialized command transaction."""

import math
from datetime import timedelta

from sqlalchemy import func, select

from team_sync import models as m
from team_sync import schemas as s
from team_sync.domain import fail, record


def actor_records(db, actors):
    if not actors:
        return []
    timestamp = db.scalar(select(func.clock_timestamp()))
    activities = {
        item.actor_id: item
        for item in db.scalars(
            select(m.Activity).where(
                m.Activity.project_id == actors[0].project_id,
                m.Activity.actor_id.in_([actor.id for actor in actors]),
            )
        )
    }
    result = []
    for actor in actors:
        activity = activities.get(actor.id)
        state = None
        if activity:
            state = {
                **record(activity),
                "stale": activity.expires_at <= timestamp,
                "expires_in_seconds": max(
                    0, math.ceil((activity.expires_at - timestamp).total_seconds())
                ),
            }
        result.append({**record(actor), "activity": state})
    return result


class WorkflowOperations:
    @staticmethod
    def task_activity(task):
        if task.kind == "artifact_review":
            if task.approval_status == "pending":
                return "waiting_human"
            if not task.analysis_started_at:
                return "ready"
        return "working"

    @staticmethod
    def unique_name(db, project_id, requested, kind, exclude=None):
        names = {
            name.strip().casefold()
            for ident, name in db.execute(
                select(m.Actor.id, m.Actor.name).where(m.Actor.project_id == project_id)
            )
            if ident != exclude
        }
        if requested:
            base = requested.strip()
        elif kind == "agent":
            base = next(
                (
                    name
                    for name in ["Nova", "Atlas", "Kira", "Orion", "Echo", "Lumen", "Mako", "Runa"]
                    if name.casefold() not in names
                ),
                "Nova",
            )
        else:
            base = "Учасник"
        candidate, number = base, 2
        while candidate.casefold() in names:
            suffix = f" {number}"
            candidate = base[: 200 - len(suffix)] + suffix
            number += 1
        return candidate

    def rename_actor(self, db, project, actor, data, resource_id):
        target = self.get(db, m.Actor, project.id, resource_id)
        if actor.id != target.id and actor.role != "owner":
            fail(403, "forbidden", "Only the actor or project owner can rename this identity")
        self.check_version(target, data.expected_version)
        before = record(target)
        target.name = self.unique_name(db, project.id, data.name, target.kind, target.id)
        target.version += 1
        self._revision(db, project, actor, "actors", target, before)
        return target

    def _activity(self, db, project, caller, target, state, detail="", task=None, ttl=120):
        timestamp = db.scalar(select(func.clock_timestamp()))
        item = db.scalar(
            select(m.Activity).where(
                m.Activity.project_id == project.id, m.Activity.actor_id == target.id
            )
        )
        if item is None:
            item = self.insert(
                db,
                project,
                m.Activity,
                actor_id=target.id,
                state=state,
                detail=detail[:300],
                task_id=task.id if task else None,
                updated_at=timestamp,
                expires_at=timestamp + timedelta(seconds=ttl),
            )
        else:
            item.state, item.detail = state, detail[:300]
            item.task_id = task.id if task else None
            item.updated_at, item.expires_at = timestamp, timestamp + timedelta(seconds=ttl)
            item.version += 1
        self.emit(
            db, project, caller, "activity.update", {"resource_id": item.id, "actor_id": target.id}
        )
        return item

    def report_activity(self, db, project, actor, data, resource_id):
        task = self.get(db, m.Task, project.id, data.task_id) if data.task_id else None
        if task and task.assignee_id != actor.id:
            fail(403, "not_assignee", "Report only your own task activity")
        return self._activity(
            db, project, actor, actor, data.state, data.detail, task, data.ttl_seconds
        )

    def _after_task(self, db, project, actor):
        timestamp = db.scalar(select(func.clock_timestamp()))
        other = db.scalar(
            select(m.Task)
            .where(
                m.Task.project_id == project.id,
                m.Task.assignee_id == actor.id,
                m.Task.status == "running",
                m.Task.lease_until > timestamp,
            )
            .order_by(m.Task.created_at.desc())
            .limit(1)
        )
        self._activity(
            db,
            project,
            actor,
            actor,
            self.task_activity(other) if other else "idle",
            other.title if other else "Завершив поточний крок",
            other,
        )

    def _notice(
        self,
        db,
        project,
        actor,
        recipient_id,
        body,
        task=None,
        kind="status",
        priority="normal",
        control=False,
    ):
        data = s.MessageCreate(
            recipient_id=recipient_id,
            body=body,
            kind=kind,
            priority=priority,
            task_id=task.id if task else None,
            group_key=f"task:{task.id}:{kind}" if task else None,
        )
        if control:
            # A human decision must remain possible even when senders exhausted their budget.
            message = self.insert(
                db,
                project,
                m.Message,
                sender_id=actor.id,
                **data.model_dump(exclude={"deduplicate"}),
            )
        else:
            from team_sync.attention import create_message

            message = create_message(self, db, project, actor, data)
        self.emit(
            db,
            project,
            actor,
            getattr(message, "_event_type", "message.send"),
            {"resource_id": message.id, "kind": kind, "priority": priority},
        )
        return message

    def assign_task(self, db, project, actor, data, resource_id):
        task = self.get(db, m.Task, project.id, resource_id)
        self.check_version(task, data.expected_version)
        if actor.role not in {"owner", "coordinator"} and task.created_by_id != actor.id:
            fail(403, "forbidden", "Only the creator or coordinator may change the recipient")
        timestamp = db.scalar(select(func.clock_timestamp()))
        if task.status == "done" or (
            task.status == "running" and task.lease_until and task.lease_until > timestamp
        ):
            fail(409, "task_in_progress", "Release a live task before changing its recipient")
        target = self.get(db, m.Actor, project.id, data.recipient_id) if data.recipient_id else None
        if target and (not target.active or target.role == "observer"):
            fail(409, "inactive_recipient", "Select an active participant with permission to work")
        if task.kind == "artifact_review" and target and target.kind != "agent":
            fail(422, "agent_required", "Artifact analysis must be assigned to an agent")
        task.requested_assignee_id = target.id if target else None
        task.status, task.assignee_id, task.lease_until = "open", None, None
        if task.kind == "artifact_review":
            task.approval_status, task.approved_by_id, task.analysis_started_at = (
                "pending",
                None,
                None,
            )
        task.version += 1
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

    def _human_approver(self, db, project, artifact, required=True):
        uploader = self.get(db, m.Actor, project.id, artifact.author_id)
        if uploader.kind == "human" and uploader.active and uploader.role != "observer":
            return uploader
        approver = db.scalar(
            select(m.Actor)
            .where(
                m.Actor.project_id == project.id,
                m.Actor.kind == "human",
                m.Actor.active.is_(True),
                m.Actor.role.in_(["owner", "coordinator"]),
            )
            .order_by(m.Actor.created_at, m.Actor.id)
            .limit(1)
        )
        if approver is None and required:
            fail(
                409,
                "human_approval_unavailable",
                "A human uploader or coordinator must approve analysis",
            )
        return approver

    def _approval_request(self, db, project, actor, task):
        artifact = self.get(db, m.Artifact, project.id, task.artifact_id)
        approver = self._human_approver(db, project, artifact)
        self._notice(
            db,
            project,
            actor,
            approver.id,
            f"{actor.name} взяв артефакт «{artifact.name}» і просить дозвіл на аналіз. "
            + ("Захищений вміст ще недоступний цьому агенту. " if artifact.requires_review else "")
            + "Перегляньте запит у Файлах.",
            task,
            "approval",
            "urgent",
        )

    def review_request(self, db, project, actor, data, resource_id):
        artifact = self.get(db, m.Artifact, project.id, resource_id)
        if artifact.archived:
            fail(
                409, "artifact_archived", "Restore the archived artifact before requesting analysis"
            )
        target_id = data.recipient_id or (actor.id if actor.kind == "agent" else None)
        target = self.get(db, m.Actor, project.id, target_id) if target_id else None
        if target and (target.kind != "agent" or not target.active or target.role == "observer"):
            fail(422, "agent_required", "Select an active agent for artifact analysis")
        self._human_approver(db, project, artifact)
        task = db.scalar(
            select(m.Task)
            .where(
                m.Task.project_id == project.id,
                m.Task.artifact_id == artifact.id,
                m.Task.kind == "artifact_review",
                m.Task.status != "done",
            )
            .order_by(m.Task.created_at)
            .limit(1)
        )
        if task is not None:
            if target_id and task.requested_assignee_id not in (None, target_id):
                fail(409, "review_in_progress", "An artifact already has an assigned review")
        else:
            task = self.insert(
                db,
                project,
                m.Task,
                title=f"Аналіз: {artifact.name}"[:200],
                description="Після дозволу людини: проаналізувати артефакт і опублікувати README. "
                "Непридатний матеріал архівується з поясненням.",
                artifact_id=artifact.id,
                kind="artifact_review",
                approval_status="pending",
                created_by_id=actor.id,
                requested_assignee_id=target_id,
            )
            self.emit(
                db, project, actor, "task.create", {"resource_id": task.id, "version": task.version}
            )
            if target and target.id != actor.id:
                self._notice(
                    db,
                    project,
                    actor,
                    target.id,
                    f"{actor.name} запропонував аналіз артефакту «{artifact.name}». "
                    "Візьміть роботу й дочекайтеся дозволу людини.",
                    task,
                    "assignment",
                )
        if actor.kind == "agent" and target_id == actor.id:
            timestamp = db.scalar(select(func.clock_timestamp()))
            if task.status == "open" or not task.lease_until or task.lease_until <= timestamp:
                self.claim(
                    db,
                    project,
                    actor,
                    s.Lease(expected_version=task.version, lease_seconds=3600),
                    task.id,
                )
        return task

    def review_decide(self, db, project, actor, data, resource_id):
        task = self.get(db, m.Task, project.id, resource_id)
        self.check_version(task, data.expected_version)
        if task.kind != "artifact_review" or task.status != "running" or not task.assignee_id:
            fail(409, "review_not_claimed", "An agent must claim this review first")
        if not task.lease_until or task.lease_until <= db.scalar(select(func.clock_timestamp())):
            fail(409, "lease_invalid", "The agent must renew or reclaim this review first")
        artifact = self.get(db, m.Artifact, project.id, task.artifact_id)
        if (
            actor.kind != "human"
            or actor.role == "observer"
            or (actor.id != artifact.author_id and actor.role not in {"owner", "coordinator"})
        ):
            fail(
                403,
                "human_approval_required",
                "Only the human uploader or human coordinator can decide",
            )
        if task.approval_status != "pending":
            fail(409, "review_already_decided", "This review already has a decision")
        assignee = self.get(db, m.Actor, project.id, task.assignee_id)
        if not assignee.active:
            fail(409, "inactive_recipient", "The assigned agent has been revoked")
        task.approval_status, task.approved_by_id, task.approval_note = (
            data.decision,
            actor.id,
            data.note,
        )
        task.version += 1
        if data.decision == "declined":
            task.status, task.lease_until = "done", None
            task.result = "Людина відхилила аналіз. " + data.note
        self._activity(
            db,
            project,
            actor,
            assignee,
            "ready" if data.decision == "approved" else "idle",
            "Отримано дозвіл; агент ще має розпочати аналіз"
            if data.decision == "approved"
            else "Аналіз не дозволено",
            task if data.decision == "approved" else None,
        )
        self._notice(
            db,
            project,
            actor,
            assignee.id,
            f"{actor.name}: {'дозволено' if data.decision == 'approved' else 'відхилено'} "
            f"аналіз «{artifact.name}». " + data.note,
            task,
            "approval",
            "urgent",
            control=True,
        )
        for message in db.scalars(
            select(m.Message).where(
                m.Message.project_id == project.id,
                m.Message.task_id == task.id,
                m.Message.kind == "approval",
                m.Message.recipient_id == actor.id,
            )
        ):
            self.ack_message(
                db,
                project,
                actor,
                s.MessageAck(stage="processed", expected_version=message.version),
                message.id,
            )
            self.emit(
                db,
                project,
                actor,
                "message.ack",
                {
                    "resource_id": message.id,
                    "stage": "processed",
                    "message_version": message.version,
                },
            )
        return task

    def _approved_review(self, db, project, actor, data, resource_id):
        task, _ = self.owned_task(db, project, actor, data, resource_id)
        if (
            actor.kind != "agent"
            or task.kind != "artifact_review"
            or task.approval_status != "approved"
        ):
            fail(403, "analysis_not_approved", "A human must approve this agent's review first")
        artifact = self.get(db, m.Artifact, project.id, task.artifact_id)
        if artifact.archived:
            fail(409, "artifact_archived", "The artifact is archived")
        return task, artifact

    def review_start(self, db, project, actor, data, resource_id):
        task, artifact = self._approved_review(db, project, actor, data, resource_id)
        if task.analysis_started_at is not None:
            fail(
                409,
                "analysis_already_started",
                "Continue the existing analysis with its current version",
            )
        task.analysis_started_at = db.scalar(select(func.clock_timestamp()))
        task.version += 1
        self._activity(db, project, actor, actor, "working", f"Аналізує: {artifact.name}", task)
        return task

    def review_complete(self, db, project, actor, data, resource_id):
        task, artifact = self._approved_review(db, project, actor, data, resource_id)
        if task.analysis_started_at is None:
            fail(
                409,
                "analysis_not_started",
                "Explicitly start the approved analysis before publishing its report",
            )
        if data.verdict == "unusable" and not data.reason.strip():
            fail(422, "archive_reason_required", "Explain why the artifact is unusable")
        text = data.readme
        if data.verdict == "unusable":
            text += "\n\n## Причина архівування\n" + data.reason
        report = self.insert(
            db,
            project,
            m.Finding,
            title=f"README: {artifact.name}"[:200],
            body=text,
            evidence=f"artifact:{artifact.id}",
            kind="result",
            artifact_id=artifact.id,
            task_id=task.id,
            challenge_id=task.challenge_id,
            author_id=actor.id,
        )
        self.emit(db, project, actor, "finding.create", {"resource_id": report.id})
        if data.verdict == "unusable":
            before = record(artifact)
            artifact.archived, artifact.archive_reason, artifact.archived_by_id = (
                True,
                data.reason,
                actor.id,
            )
            artifact.version += 1
            self._revision(db, project, actor, "artifacts", artifact, before, data.reason)
            self.emit(
                db,
                project,
                actor,
                "artifact.archive",
                {"resource_id": artifact.id, "version": artifact.version},
            )
        task.status, task.lease_until = "done", None
        task.result = f"README: finding:{report.id}. Висновок: {data.verdict}."
        task.version += 1
        self._after_task(db, project, actor)
        recipient = self._human_approver(db, project, artifact, required=False)
        if recipient:
            self._notice(
                db,
                project,
                actor,
                recipient.id,
                f"{actor.name} завершив аналіз «{artifact.name}». README збережено. "
                + (
                    "Артефакт архівовано з причиною."
                    if artifact.archived
                    else "Артефакт збережено для команди."
                ),
                task,
                "result",
            )
        return task

    def archive_artifact(self, db, project, actor, data, resource_id):
        artifact = self.get(db, m.Artifact, project.id, resource_id)
        self.check_version(artifact, data.expected_version)
        before = record(artifact)
        if actor.kind != "human" or (
            actor.id != artifact.author_id and actor.role not in {"owner", "coordinator"}
        ):
            fail(
                403,
                "human_action_required",
                "A human uploader or coordinator can archive or restore",
            )
        artifact.archived, artifact.archive_reason, artifact.archived_by_id = (
            data.archived,
            data.reason,
            actor.id,
        )
        artifact.version += 1
        self._revision(db, project, actor, "artifacts", artifact, before, data.reason)
        if data.archived:
            for task in db.scalars(
                select(m.Task).where(
                    m.Task.project_id == project.id,
                    m.Task.artifact_id == artifact.id,
                    m.Task.kind == "artifact_review",
                    m.Task.status != "done",
                )
            ):
                task.status, task.lease_until = "done", None
                task.approval_status = "declined"
                task.approved_by_id = actor.id
                task.approval_note = data.reason
                task.result = f"Людина архівувала артефакт: {data.reason}"
                task.version += 1
                self.emit(
                    db,
                    project,
                    actor,
                    "artifact.review.decide",
                    {
                        "resource_id": task.id,
                        "artifact_id": artifact.id,
                        "version": task.version,
                        "status": task.status,
                        "approval_status": task.approval_status,
                    },
                )
                if task.assignee_id:
                    self._notice(
                        db,
                        project,
                        actor,
                        task.assignee_id,
                        task.result,
                        task,
                        "approval",
                        "urgent",
                        control=True,
                    )
                    target = self.get(db, m.Actor, project.id, task.assignee_id)
                    self._activity(
                        db,
                        project,
                        actor,
                        target,
                        "ready",
                        "Людина архівувала артефакт; цей аналіз завершено",
                    )
        finding = self.insert(
            db,
            project,
            m.Finding,
            author_id=actor.id,
            artifact_id=artifact.id,
            title=f"{'Архів' if data.archived else 'Відновлено'}: {artifact.name}"[:200],
            body=data.reason,
            evidence=f"artifact:{artifact.id}",
            kind="observation",
        )
        self.emit(db, project, actor, "finding.create", {"resource_id": finding.id})
        return artifact
