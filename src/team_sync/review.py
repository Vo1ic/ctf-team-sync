"""Verified completion, human-approved hints and stalled-work detection.

A challenge task is finished only when a human confirmed a correct flag: completing it
without one moves it to ``review``. Confirming a flag finishes the waiting tasks;
rejecting the last candidate returns them to their assignee.
"""

from sqlalchemy import func, select

from team_sync import models as m
from team_sync.domain import fail

LEADS = {"owner", "coordinator"}


class ReviewOperations:
    # ----- flag-gated completion -----------------------------------------------------------

    @staticmethod
    def confirmed_flag(db, project_id, challenge_id):
        return db.scalar(
            select(m.Flag.id)
            .where(
                m.Flag.project_id == project_id,
                m.Flag.challenge_id == challenge_id,
                m.Flag.verification_status == "confirmed",
            )
            .limit(1)
        )

    def needs_flag(self, project, task, without_flag=False):
        return bool(
            project.capabilities.get("flags")
            and task.challenge_id
            and task.kind == "work"
            and not without_flag
        )

    def human_leads(self, db, project):
        return list(
            db.scalars(
                select(m.Actor)
                .where(
                    m.Actor.project_id == project.id,
                    m.Actor.kind == "human",
                    m.Actor.active.is_(True),
                    m.Actor.role.in_(sorted(LEADS)),
                )
                .order_by(m.Actor.created_at)
            )
        )

    def request_flag_review(self, db, project, actor, task):
        for lead in self.human_leads(db, project):
            if lead.id == actor.id:
                continue
            self._notice(
                db,
                project,
                actor,
                lead.id,
                f"{actor.name} завершив «{task.title}». Робота чекає підтвердження флага — "
                "без нього завдання не зараховується.",
                task,
                "approval",
            )

    def settle_reviews(self, db, project, actor, flag):
        challenge_id = flag.challenge_id
        if not challenge_id and flag.task_id:
            challenge_id = self.get(db, m.Task, project.id, flag.task_id).challenge_id
        if not challenge_id:
            return
        waiting = list(
            db.scalars(
                select(m.Task).where(
                    m.Task.project_id == project.id,
                    m.Task.challenge_id == challenge_id,
                    m.Task.status == "review",
                )
            )
        )
        if flag.verification_status == "rejected":
            alive = db.scalar(
                select(m.Flag.id)
                .where(
                    m.Flag.project_id == project.id,
                    m.Flag.challenge_id == challenge_id,
                    m.Flag.id != flag.id,
                    m.Flag.verification_status.in_(["unverified", "confirmed"]),
                )
                .limit(1)
            )
            if alive:
                return  # another candidate may still be the right one
        if flag.verification_status == "confirmed":
            self.close_solved(db, project, actor, challenge_id)
        for task in waiting:
            assignee = task.assignee_id
            if flag.verification_status == "confirmed":
                task.status = "done"
                event, text, priority = (
                    "task.verified",
                    f"Флаг підтверджено — «{task.title}» зараховано.",
                    "normal",
                )
            else:
                task.status, task.assignee_id = "open", None
                task.requested_assignee_id = assignee
                event, text, priority = (
                    "task.reopened",
                    f"Флаг для «{task.title}» відхилено. Роботу повернуто вам: "
                    "перевірте наявні знахідки й продовжуйте.",
                    "urgent",
                )
            task.version += 1
            self.emit(
                db,
                project,
                actor,
                event,
                {
                    "resource_id": task.id,
                    "resource_type": "tasks",
                    "status": task.status,
                    "version": task.version,
                    "flag_id": flag.id,
                },
            )
            if assignee and assignee != actor.id:
                self._notice(db, project, actor, assignee, text, task, "status", priority)

    def close_solved(self, db, project, actor, challenge_id):
        """A confirmed flag makes remaining work on the challenge pointless.

        Unclaimed tasks are closed so runners stop starting agents on them; people and
        agents still holding a lease are told to stop.
        """
        now = db.scalar(select(func.clock_timestamp()))
        for task in db.scalars(
            select(m.Task).where(
                m.Task.project_id == project.id,
                m.Task.challenge_id == challenge_id,
                m.Task.kind == "work",
                m.Task.status.in_(["open", "running"]),
            )
        ):
            live = task.status == "running" and task.lease_until and task.lease_until > now
            if live:
                if task.assignee_id and task.assignee_id != actor.id:
                    self._notice(
                        db,
                        project,
                        actor,
                        task.assignee_id,
                        f"Флаг для «{task.title}» уже підтверджено. Зупиніться й завершіть "
                        "роботу з without_flag=true або відпустіть її.",
                        task,
                        "status",
                        "urgent",
                    )
                continue
            task.status, task.assignee_id, task.lease_until = "done", None, None
            task.requested_assignee_id = None
            task.result = task.result or "Закрито автоматично: флаг завдання підтверджено."
            task.version += 1
            self.emit(
                db,
                project,
                actor,
                "task.closed",
                {
                    "resource_id": task.id,
                    "resource_type": "tasks",
                    "status": "done",
                    "version": task.version,
                },
            )

    # ----- hints -------------------------------------------------------------------------

    def request_hint(self, db, project, actor, data, resource_id):
        task = self.get(db, m.Task, project.id, data.task_id) if data.task_id else None
        challenge_id = data.challenge_id or (task.challenge_id if task else None)
        if not challenge_id:
            fail(422, "challenge_required", "A hint request must name its challenge")
        if task and data.challenge_id and task.challenge_id != data.challenge_id:
            fail(422, "scope_mismatch", "Task does not belong to the challenge")
        challenge = self.get(db, m.Challenge, project.id, challenge_id)
        evidence = db.scalar(
            select(func.count())
            .select_from(m.Finding)
            .where(
                m.Finding.project_id == project.id,
                m.Finding.challenge_id == challenge.id,
                m.Finding.author_id == actor.id,
            )
        )
        if not evidence:
            fail(
                409,
                "evidence_required",
                "Publish what you already tested (report_finding with evidence) before "
                "asking for a hint",
            )
        pending = db.scalar(
            select(m.HintRequest.id).where(
                m.HintRequest.project_id == project.id,
                m.HintRequest.challenge_id == challenge.id,
                m.HintRequest.requester_id == actor.id,
                m.HintRequest.status == "pending",
            )
        )
        if pending:
            fail(409, "hint_pending", "You already asked for this hint", existing_id=pending)
        request = self.insert(
            db,
            project,
            m.HintRequest,
            challenge_id=challenge.id,
            task_id=task.id if task else None,
            requester_id=actor.id,
            reason=data.reason,
            hint_ref=data.hint_ref,
            cost=data.cost,
        )
        cost = f" (−{data.cost} балів)" if data.cost is not None else ""
        for lead in self.human_leads(db, project):
            if lead.id != actor.id:
                self._notice(
                    db,
                    project,
                    actor,
                    lead.id,
                    f"{actor.name} просить підказку для «{challenge.title}»{cost}. "
                    f"Причина: {data.reason[:300]}",
                    task,
                    "approval",
                    "urgent",
                )
        return request

    def decide_hint(self, db, project, actor, data, resource_id):
        request = self.get(db, m.HintRequest, project.id, resource_id)
        self.check_version(request, data.expected_version)
        if actor.kind != "human" or actor.role not in LEADS:
            fail(403, "human_approval_required", "Only a human owner or coordinator decides")
        if request.status != "pending":
            fail(409, "hint_already_decided", "This hint request already has a decision")
        request.status = data.decision
        request.decided_by_id, request.decision_note = actor.id, data.note
        request.decided_at = db.scalar(select(func.clock_timestamp()))
        request.version += 1
        challenge = self.get(db, m.Challenge, project.id, request.challenge_id)
        verdict = "дозволено" if data.decision == "approved" else "не дозволено"
        if request.requester_id != actor.id:
            self._notice(
                db,
                project,
                actor,
                request.requester_id,
                f"{actor.name}: підказку для «{challenge.title}» {verdict}. {data.note}".strip(),
                None,
                "approval",
                "urgent",
                control=True,
            )
        return request

    def hint_used(self, db, project, actor, data, resource_id):
        request = self.get(db, m.HintRequest, project.id, resource_id)
        self.check_version(request, data.expected_version)
        if request.status != "approved":
            fail(409, "hint_not_approved", "Only an approved hint can be recorded as used")
        if actor.id != request.requester_id and (actor.kind != "human" or actor.role not in LEADS):
            fail(403, "forbidden", "Only the requester or a human lead records the hint")
        approver = self.get(db, m.Actor, project.id, request.decided_by_id)
        finding = self.insert(
            db,
            project,
            m.Finding,
            author_id=actor.id,
            title=f"Підказка платформи{': ' + request.hint_ref if request.hint_ref else ''}"[:200],
            body=data.content,
            evidence=f"Підказку відкрито з дозволу {approver.name}"
            + (f", вартість {request.cost}" if request.cost is not None else ""),
            kind="observation",
            challenge_id=request.challenge_id,
            task_id=request.task_id,
        )
        self.emit(db, project, actor, "finding.create", {"resource_id": finding.id})
        request.status, request.finding_id = "used", finding.id
        request.version += 1
        return request

    # ----- stalled work -----------------------------------------------------------------

    def stalled(self, project_id, token, minutes=20):
        with self.context(project_id, token) as (db, project, _):
            now = db.scalar(select(func.clock_timestamp()))
            tasks = list(
                db.scalars(
                    select(m.Task).where(
                        m.Task.project_id == project.id,
                        m.Task.status == "running",
                        m.Task.lease_until > now,
                    )
                )
            )
            names = {
                actor.id: actor
                for actor in db.scalars(select(m.Actor).where(m.Actor.project_id == project.id))
            }
            items = []
            for task in tasks:
                claimed = db.scalar(
                    select(func.max(m.Event.created_at)).where(
                        m.Event.project_id == project.id,
                        m.Event.type.in_(["task.claim", "task.join"]),
                        m.Event.payload["resource_id"].as_string() == task.id,
                    )
                )
                progress = [
                    db.scalar(
                        select(func.max(model.created_at)).where(
                            model.project_id == project.id,
                            model.task_id == task.id,
                            model.author_id == task.assignee_id,
                        )
                    )
                    for model in (m.Finding, m.Log)
                ]
                last = max([value for value in [claimed, *progress] if value] or [now])
                idle = int((now - last).total_seconds() // 60)
                activity = db.scalar(
                    select(m.Activity).where(
                        m.Activity.project_id == project.id,
                        m.Activity.actor_id == task.assignee_id,
                        m.Activity.expires_at > now,
                    )
                )
                blocked = activity is not None and activity.state in {"blocked", "waiting_human"}
                if idle < minutes and not blocked:
                    continue
                assignee = names.get(task.assignee_id)
                items.append(
                    {
                        "task_id": task.id,
                        "title": task.title,
                        "challenge_id": task.challenge_id,
                        "assignee_id": task.assignee_id,
                        "assignee": assignee.name if assignee else None,
                        "idle_minutes": idle,
                        "last_progress_at": last.isoformat(),
                        "state": activity.state if activity else None,
                        "detail": activity.detail if activity else None,
                        "reason": "blocked" if blocked else "no_progress",
                    }
                )
            items.sort(key=lambda item: (item["reason"] != "blocked", -item["idle_minutes"]))
            return {"items": items, "minutes": minutes, "server_time": now.isoformat()}
