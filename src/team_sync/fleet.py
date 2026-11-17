"""Expert questions, runners and runs.

Agents talk freely: a question or a request is data for its recipient. Starting an
agent on someone's machine is different: a runner only starts profiles defined in its
local configuration, and a run requested by anyone but the runner's human owner waits
for that owner's decision. The server never stores or forwards a command line.
"""

from sqlalchemy import func, select

from team_sync import models as m
from team_sync.domain import fail, record

ONLINE_SECONDS = 90
MAX_OPEN_QUESTIONS = 5
MAX_PENDING_REQUESTS = 3
CONFIDENCE = {"low": "низька", "medium": "середня", "high": "висока"}


class FleetOperations:
    # ----- questions ---------------------------------------------------------------------

    def ask_question(self, db, project, actor, data, resource_id):
        parent = self.get(db, m.Task, project.id, data.task_id) if data.task_id else None
        challenge_id = data.challenge_id or (parent.challenge_id if parent else None)
        if parent and data.challenge_id and parent.challenge_id not in (None, data.challenge_id):
            fail(422, "scope_mismatch", "Task does not belong to the challenge")
        if challenge_id:
            self.get(db, m.Challenge, project.id, challenge_id)
        open_questions = list(
            db.scalars(
                select(m.Task).where(
                    m.Task.project_id == project.id,
                    m.Task.kind == "question",
                    m.Task.created_by_id == actor.id,
                    m.Task.status != "done",
                )
            )
        )
        title = data.question.strip()
        for question in open_questions:
            if question.title.strip().casefold() == title.casefold():
                fail(
                    409,
                    "duplicate_question",
                    "You already asked this question; wait for the answer",
                    existing_id=question.id,
                )
        if len(open_questions) >= MAX_OPEN_QUESTIONS:
            fail(
                429,
                "too_many_questions",
                f"At most {MAX_OPEN_QUESTIONS} open questions per participant; "
                "wait for answers or close the old ones",
            )
        question = self.insert(
            db,
            project,
            m.Task,
            kind="question",
            title=title[:200],
            description=f"{data.details.strip()}\n\n### Що вже перевірено\n{data.tried.strip()}",
            challenge_id=challenge_id,
            parent_id=parent.id if parent else None,
            tags=list(dict.fromkeys(data.skills)),
            priority=data.priority,
            created_by_id=actor.id,
        )
        for artifact_id in dict.fromkeys(data.artifact_ids):
            self._link(
                db, project, actor, "artifact", artifact_id, "task", question.id, "attached_to"
            )
        return question

    def answer_question(self, db, project, actor, data, resource_id):
        question = self.get(db, m.Task, project.id, resource_id)
        self.check_version(question, data.expected_version)
        if question.kind != "question":
            fail(422, "not_a_question", "Only a question task takes an answer")
        if question.status == "done":
            fail(409, "already_answered", "This question already has an answer")
        timestamp = db.scalar(select(func.clock_timestamp()))
        holds_lease = (
            question.status == "running"
            and question.assignee_id == actor.id
            and question.lease_until
            and question.lease_until > timestamp
        )
        if not holds_lease and actor.kind != "human" and actor.id != question.created_by_id:
            fail(409, "lease_invalid", "Claim the question before answering it")
        finding = self.insert(
            db,
            project,
            m.Finding,
            author_id=actor.id,
            title=f"Відповідь: {question.title}"[:200],
            body=data.answer,
            evidence=data.evidence or f"Впевненість експерта: {CONFIDENCE[data.confidence]}",
            kind="observation",
            challenge_id=question.challenge_id,
            task_id=question.id,
        )
        self.emit(db, project, actor, "finding.create", {"resource_id": finding.id})
        if question.parent_id:
            self._link(
                db, project, actor, "finding", finding.id, "task", question.parent_id, "leads_to"
            )
        question.status, question.assignee_id, question.lease_until = "done", actor.id, None
        question.result = data.answer
        question.version += 1
        asker = question.created_by_id
        if asker and asker != actor.id:
            self._notice(
                db,
                project,
                actor,
                asker,
                f"{actor.name} відповів на питання «{question.title}» "
                f"(впевненість: {CONFIDENCE[data.confidence]}). Повна відповідь — знахідка "
                f"{finding.id}:\n\n{data.answer[:1500]}",
                question,
                "result",
                "urgent",
            )
        self._after_task(db, project, actor)
        question._event_type = "question.answer"
        question._event_extra = {"finding_id": finding.id}
        return question

    # ----- runners -----------------------------------------------------------------------

    def _runner_owner(self, actor):
        if actor.kind != "human" or actor.role == "observer":
            fail(403, "human_runner_required", "A runner authenticates as its human owner")

    def register_runner(self, project_id, token, data):
        with self.context(project_id, token, write=True) as (db, project, actor):
            self._runner_owner(actor)
            now = db.scalar(select(func.clock_timestamp()))
            runner = db.scalar(
                select(m.Runner).where(
                    m.Runner.project_id == project.id,
                    m.Runner.owner_id == actor.id,
                    m.Runner.name == data.name,
                )
            )
            profiles = [profile.model_dump() for profile in data.profiles]
            if runner is None:
                runner = self.insert(
                    db,
                    project,
                    m.Runner,
                    owner_id=actor.id,
                    name=data.name,
                    profiles=profiles,
                    info=data.info,
                    paused=data.paused,
                    last_seen_at=now,
                )
                changed = True
            else:
                was_offline = (now - runner.last_seen_at).total_seconds() > ONLINE_SECONDS
                changed = (
                    was_offline
                    or runner.profiles != profiles
                    or runner.paused != data.paused
                    or runner.info != data.info
                )
                runner.profiles, runner.info, runner.paused = profiles, data.info, data.paused
                runner.last_seen_at = now
                if changed:
                    runner.version += 1
            if changed:
                # Heartbeats stay silent; only visible changes reach the event stream.
                self.emit(
                    db,
                    project,
                    actor,
                    "runner.update",
                    {"resource_id": runner.id, "resource_type": "runners", "paused": runner.paused},
                )
            work = list(
                db.scalars(
                    select(m.Run)
                    .where(
                        m.Run.project_id == project.id,
                        m.Run.runner_id == runner.id,
                        m.Run.status.in_(["approved", "running", "cancelled"]),
                        m.Run.finished_at.is_(None),
                    )
                    .order_by(m.Run.created_at)
                )
            )
            questions = list(
                db.scalars(
                    select(m.Task)
                    .where(
                        m.Task.project_id == project.id,
                        m.Task.kind == "question",
                        m.Task.status == "open",
                        m.Task.requested_assignee_id.is_(None),
                    )
                    .order_by(m.Task.priority.desc(), m.Task.created_at)
                    .limit(20)
                )
            )
            busy = set(
                db.scalars(
                    select(m.Run.task_id).where(
                        m.Run.project_id == project.id,
                        m.Run.status.in_(m.ACTIVE_RUN_STATES),
                        m.Run.task_id.is_not(None),
                    )
                )
            )
            categories = dict(
                db.execute(
                    select(m.Challenge.id, m.Challenge.category).where(
                        m.Challenge.project_id == project.id
                    )
                ).all()
            )
            solved = select(m.Flag.challenge_id).where(
                m.Flag.project_id == project.id, m.Flag.verification_status == "confirmed"
            )
            open_work = list(
                db.scalars(
                    select(m.Task)
                    .where(
                        m.Task.project_id == project.id,
                        m.Task.kind == "work",
                        m.Task.status == "open",
                        (m.Task.challenge_id.is_(None)) | (m.Task.challenge_id.not_in(solved)),
                    )
                    .order_by(m.Task.priority.desc(), m.Task.created_at)
                    .limit(40)
                )
            )
            coordinator = db.scalar(
                select(m.Run.id).where(
                    m.Run.project_id == project.id,
                    m.Run.purpose == "coordinator",
                    m.Run.status.in_(m.ACTIVE_RUN_STATES),
                )
            )
            return {
                "runner": record(runner),
                "runs": [record(run) for run in work],
                "questions": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "tags": task.tags or [],
                        "priority": task.priority,
                        "challenge_id": task.challenge_id,
                    }
                    for task in questions
                    if task.id not in busy
                ],
                "tasks": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "priority": task.priority,
                        "challenge_id": task.challenge_id,
                        "category": categories.get(task.challenge_id),
                        "requested_assignee_id": task.requested_assignee_id,
                    }
                    for task in open_work
                    if task.id not in busy
                ][:20],
                "coordinator_active": bool(coordinator),
                "event_seq": project.event_seq,
                "server_time": now.isoformat(),
            }

    def update_run_state(self, project_id, token, run_id, data):
        with self.context(project_id, token, write=True) as (db, project, actor):
            run = self.get(db, m.Run, project.id, run_id)
            runner = self.get(db, m.Runner, project.id, run.runner_id)
            if runner.owner_id != actor.id:
                fail(403, "forbidden", "Only the runner's owner reports its runs")
            now = db.scalar(select(func.clock_timestamp()))
            if data.status == "running":
                if run.status != "approved":
                    fail(409, "invalid_transition", f"Run is {run.status}")
                run.status, run.started_at = "running", now
            else:
                if run.finished_at is not None or run.status not in {
                    "approved",
                    "running",
                    "cancelled",
                }:
                    fail(409, "invalid_transition", f"Run is {run.status}")
                if run.status != "cancelled":
                    run.status = data.status
                run.finished_at, run.exit_code = now, data.exit_code
            run.output_tail = data.output_tail[-20_000:]
            if data.agent_name:
                run.agent_name = data.agent_name
            run.version += 1
            if data.status != "running":
                self._release_dead_agent(db, project, actor, run, data.agent_id)
            self.emit(
                db,
                project,
                actor,
                "run.update",
                {"resource_id": run.id, "resource_type": "runs", "status": run.status},
            )
            if data.status != "running" and run.requested_by_id not in (actor.id, None):
                outcome = {"finished": "завершився", "failed": "завершився з помилкою"}.get(
                    run.status, "зупинено"
                )
                self._notice(
                    db,
                    project,
                    actor,
                    run.requested_by_id,
                    f"Запуск «{run.profile}» на {runner.name} {outcome}"
                    + (f" (код {run.exit_code})" if run.exit_code is not None else "")
                    + ".",
                    None,
                    "result",
                )
            return record(run)

    def _release_dead_agent(self, db, project, owner, run, agent_id):
        """The agent process is gone: its work goes back to the team at once instead of
        sitting behind a lease nobody renews."""
        query = select(m.Actor).where(
            m.Actor.project_id == project.id,
            m.Actor.kind == "agent",
            m.Actor.owner_id == owner.id,
        )
        if agent_id:
            query = query.where(m.Actor.id == agent_id)
        elif run.agent_name:
            query = query.where(m.Actor.name == run.agent_name)
        else:
            return
        agent = db.scalar(query.limit(1))
        if agent is None:
            return
        outcome = {"finished": "завершився", "failed": "завершився з помилкою"}.get(
            run.status, "зупинено"
        )
        for task in db.scalars(
            select(m.Task).where(
                m.Task.project_id == project.id,
                m.Task.assignee_id == agent.id,
                m.Task.status == "running",
            )
        ):
            finding = self.insert(
                db,
                project,
                m.Finding,
                author_id=owner.id,
                title=f"Handoff: {task.title}"[:200],
                body=(
                    f"[runner] Процес агента {agent.name} {outcome}"
                    + (f" (код {run.exit_code})" if run.exit_code is not None else "")
                    + ", а робота лишалась у нього. Її повернено команді; що він встиг, "
                    "дивіться в знахідках і журналі."
                ),
                evidence=f"Run {run.id} ({run.profile}) ended without completing the task.",
                kind="observation",
                task_id=task.id,
                challenge_id=task.challenge_id,
                artifact_id=task.artifact_id,
            )
            self.emit(db, project, owner, "finding.create", {"resource_id": finding.id})
            task.status, task.assignee_id, task.lease_until = "open", None, None
            if task.kind == "artifact_review":
                task.approval_status, task.approved_by_id, task.analysis_started_at = (
                    "pending",
                    None,
                    None,
                )
            task.version += 1
            self.emit(
                db,
                project,
                owner,
                "task.release",
                {"resource_id": task.id, "resource_type": "tasks", "version": task.version},
            )
        # The status it last reported described a process that no longer exists.
        self._activity(db, project, owner, agent, "idle", f"Процес агента {outcome}", ttl=600)

    # ----- runs --------------------------------------------------------------------------

    def _online(self, runner, now):
        return (now - runner.last_seen_at).total_seconds() <= ONLINE_SECONDS

    def request_run(self, db, project, actor, data, resource_id):
        now = db.scalar(select(func.clock_timestamp()))
        human = actor.id if actor.kind == "human" else actor.owner_id
        if data.runner_id:
            runners = [self.get(db, m.Runner, project.id, data.runner_id)]
        else:
            runners = [
                runner
                for runner in db.scalars(
                    select(m.Runner)
                    .where(m.Runner.project_id == project.id)
                    .order_by(m.Runner.last_seen_at.desc())
                )
                if self._online(runner, now)
                and not runner.paused
                and any(item["name"] == data.profile for item in runner.profiles)
            ]
            # Prefer a machine of the same human: it needs no one else's approval.
            runners.sort(key=lambda runner: runner.owner_id != human)
            if not runners:
                fail(409, "no_runner", f"No online runner offers the profile {data.profile}")
        runner = runners[0]
        profile = next((item for item in runner.profiles if item["name"] == data.profile), None)
        if profile is None:
            fail(422, "unknown_profile", f"Runner {runner.name} does not offer {data.profile}")
        task = self.get(db, m.Task, project.id, data.task_id) if data.task_id else None
        if task and task.status == "done":
            fail(409, "task_done", "This task is already finished")
        if profile.get("coordinator"):
            purpose = "coordinator"
        elif task:
            purpose = "question" if task.kind == "question" else "task"
        else:
            purpose = "manual"
        active = select(m.Run.id).where(
            m.Run.project_id == project.id, m.Run.status.in_(m.ACTIVE_RUN_STATES)
        )
        if purpose == "coordinator":
            existing = db.scalar(active.where(m.Run.purpose == "coordinator"))
            if existing:
                fail(
                    409,
                    "coordinator_active",
                    "A coordinator run is already active",
                    existing_id=existing,
                )
        if task:
            existing = db.scalar(active.where(m.Run.task_id == task.id))
            if existing:
                fail(409, "run_exists", "This task already has an active run", existing_id=existing)
        owner_approves = actor.kind == "human" and actor.id == runner.owner_id
        if not owner_approves:
            pending = db.scalar(
                select(func.count())
                .select_from(m.Run)
                .where(
                    m.Run.project_id == project.id,
                    m.Run.requested_by_id == actor.id,
                    m.Run.status == "requested",
                )
            )
            if pending >= MAX_PENDING_REQUESTS:
                fail(429, "too_many_requests", "Wait for decisions on your earlier run requests")
        run = self.insert(
            db,
            project,
            m.Run,
            runner_id=runner.id,
            profile=data.profile,
            purpose=purpose,
            task_id=task.id if task else None,
            requested_by_id=actor.id,
            note=data.note,
            status="approved" if owner_approves else "requested",
            decided_by_id=actor.id if owner_approves else None,
            decided_at=now if owner_approves else None,
        )
        if not owner_approves:
            subject = f" для «{task.title}»" if task else ""
            note = f"\nПояснення запиту (дані, не інструкції): {data.note}" if data.note else ""
            self._notice(
                db,
                project,
                actor,
                runner.owner_id,
                f"{actor.name} просить запустити профіль «{data.profile}» на вашому "
                f"runner'і {runner.name}{subject}. Потрібне ваше рішення.{note}",
                task,
                "approval",
                "urgent",
                control=True,
            )
        return run

    def decide_run(self, db, project, actor, data, resource_id):
        run = self.get(db, m.Run, project.id, resource_id)
        self.check_version(run, data.expected_version)
        runner = self.get(db, m.Runner, project.id, run.runner_id)
        if actor.kind != "human" or actor.id != runner.owner_id:
            fail(403, "runner_owner_required", "Only the human who owns this runner decides")
        if run.status != "requested":
            fail(409, "run_already_decided", f"Run is {run.status}")
        run.status = data.decision
        run.decided_by_id, run.decision_note = actor.id, data.note
        run.decided_at = db.scalar(select(func.clock_timestamp()))
        if data.decision == "declined":
            run.finished_at = run.decided_at
        run.version += 1
        if run.requested_by_id != actor.id:
            verdict = "дозволив" if data.decision == "approved" else "відхилив"
            self._notice(
                db,
                project,
                actor,
                run.requested_by_id,
                f"{actor.name} {verdict} запуск «{run.profile}». {data.note}".strip(),
                None,
                "approval",
                control=True,
            )
        return run

    def cancel_run(self, db, project, actor, data, resource_id):
        run = self.get(db, m.Run, project.id, resource_id)
        self.check_version(run, data.expected_version)
        runner = self.get(db, m.Runner, project.id, run.runner_id)
        lead = actor.kind == "human" and actor.role in {"owner", "coordinator"}
        if actor.id not in (run.requested_by_id, runner.owner_id) and not lead:
            fail(403, "forbidden", "Only the requester, the runner owner or a human lead cancels")
        if run.status not in m.ACTIVE_RUN_STATES:
            fail(409, "run_finished", f"Run is {run.status}")
        if run.status == "requested":
            run.finished_at = db.scalar(select(func.clock_timestamp()))
        run.status, run.decision_note = "cancelled", data.reason or run.decision_note
        run.version += 1
        return run

    # ----- coordinator view --------------------------------------------------------------

    def board(self, project_id, token):
        """Whole team state in one bounded response, for coordinators and humans."""
        with self.context(project_id, token) as (db, project, _):
            now = db.scalar(select(func.clock_timestamp()))
            actors = {
                item.id: item
                for item in db.scalars(select(m.Actor).where(m.Actor.project_id == project.id))
            }

            def name(actor_id):
                return actors[actor_id].name if actor_id in actors else None

            challenges = list(
                db.scalars(
                    select(m.Challenge)
                    .where(m.Challenge.project_id == project.id)
                    .order_by(m.Challenge.created_at)
                )
            )
            tasks = list(
                db.scalars(
                    select(m.Task)
                    .where(m.Task.project_id == project.id)
                    .order_by(m.Task.created_at)
                )
            )
            flags = list(db.scalars(select(m.Flag).where(m.Flag.project_id == project.id)))
            solved = {f.challenge_id for f in flags if f.verification_status == "confirmed"}
            candidates = {f.challenge_id for f in flags if f.verification_status == "unverified"}
            findings = dict(
                db.execute(
                    select(m.Finding.challenge_id, func.count())
                    .where(m.Finding.project_id == project.id)
                    .group_by(m.Finding.challenge_id)
                ).all()
            )

            def challenge_state(challenge):
                if challenge.id in solved:
                    return "solved"
                own = [t for t in tasks if t.challenge_id == challenge.id and t.kind == "work"]
                if challenge.id in candidates or any(t.status == "review" for t in own):
                    return "flag_review"
                if any(t.status == "running" for t in own):
                    return "in_progress"
                return "open" if own else "no_task"

            def task_row(task):
                live = task.lease_until and task.lease_until > now
                return {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status if task.status != "running" or live else "lease_expired",
                    "kind": task.kind,
                    "challenge_id": task.challenge_id,
                    "assignee": name(task.assignee_id),
                    "requested_assignee": name(task.requested_assignee_id),
                    "priority": task.priority,
                    **({"tags": task.tags} if task.tags else {}),
                }

            runners = list(db.scalars(select(m.Runner).where(m.Runner.project_id == project.id)))
            runs = list(
                db.scalars(
                    select(m.Run).where(
                        m.Run.project_id == project.id, m.Run.status.in_(m.ACTIVE_RUN_STATES)
                    )
                )
            )
            activities = {
                a.actor_id: a
                for a in db.scalars(
                    select(m.Activity).where(
                        m.Activity.project_id == project.id, m.Activity.expires_at > now
                    )
                )
            }
            hints = db.scalar(
                select(func.count())
                .select_from(m.HintRequest)
                .where(m.HintRequest.project_id == project.id, m.HintRequest.status == "pending")
            )
            unfinished = [t for t in tasks if t.status != "done"]
            return {
                "project": {"id": project.id, "name": project.name},
                "server_time": now.isoformat(),
                "event_seq": project.event_seq,
                "challenges": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "category": c.category,
                        "points": c.points,
                        "state": challenge_state(c),
                        "findings": findings.get(c.id, 0),
                    }
                    for c in challenges
                ][:200],
                "tasks": [task_row(t) for t in unfinished if t.kind != "question"][:150],
                "questions": [task_row(t) for t in unfinished if t.kind == "question"][:50],
                "pending_hints": hints,
                "participants": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "kind": a.kind,
                        "role": a.role,
                        "activity": activities[a.id].state if a.id in activities else None,
                        "detail": activities[a.id].detail if a.id in activities else None,
                    }
                    for a in actors.values()
                    if a.active
                ],
                "runners": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "owner": name(r.owner_id),
                        "online": self._online(r, now),
                        "paused": r.paused,
                        "profiles": [
                            {
                                k: p.get(k)
                                for k in (
                                    "name",
                                    "description",
                                    "skills",
                                    "answers_questions",
                                    "coordinator",
                                    "model",
                                )
                            }
                            for p in r.profiles
                        ],
                    }
                    for r in runners
                ],
                "active_runs": [
                    {
                        "id": r.id,
                        "profile": r.profile,
                        "status": r.status,
                        "purpose": r.purpose,
                        "task_id": r.task_id,
                        "requested_by": name(r.requested_by_id),
                    }
                    for r in runs
                ],
                "next_steps": [
                    "Solve order: untouched high-value challenges first, then stalled ones.",
                    "Assign with task.create(requested_assignee_id) or message people; a "
                    "human confirms flags and approves hints and runs on other machines.",
                ],
            }
