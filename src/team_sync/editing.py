"""Versioned edits of current projections, with immutable project-scoped revisions."""

import json

from sqlalchemy import func, select

from team_sync import models as m
from team_sync import schemas as s
from team_sync.domain import DomainError, digest, fail, record

MODELS = {
    name: model
    for name, model in [
        ("projects", m.Project),
        ("actors", m.Actor),
        ("challenges", m.Challenge),
        ("tasks", m.Task),
        ("findings", m.Finding),
        ("flags", m.Flag),
        ("verifications", m.Verification),
        ("submissions", m.Submission),
        ("messages", m.Message),
        ("artifacts", m.Artifact),
        ("activities", m.Activity),
        ("logs", m.Log),
    ]
}
FIELDS = {
    "projects": s.ProjectFields,
    "actors": s.ActorFields,
    "challenges": s.ChallengeFields,
    "tasks": s.TaskFields,
    "findings": s.FindingFields,
    "flags": s.FlagFields,
    "verifications": s.VerificationFields,
    "submissions": s.SubmissionFields,
    "messages": s.MessageFields,
    "artifacts": s.ArtifactFields,
    "activities": s.ActivityFields,
}
EVENT_NAMES = {
    "projects": "project",
    "actors": "actor",
    "challenges": "challenge",
    "tasks": "task",
    "findings": "finding",
    "flags": "flag",
    "verifications": "verification",
    "submissions": "submission",
    "messages": "message",
    "artifacts": "artifact",
    "activities": "activity",
}


def flag_subject(flag):
    return {field: getattr(flag, field) for field in ("value", "challenge_id", "target", "round")}


def receipt_stage(receipt, message):
    return receipt.stage if receipt and receipt.message_version == message.version else None


class EditingOperations:
    def editing_info(self, actor, item, collection):
        if collection not in FIELDS:
            return {"allowed": False, "fields": [], "expected_version": item.version}
        allowed = []
        for field in FIELDS[collection].model_fields:
            try:
                self.edit_permission(actor, item, collection, {field: None})
            except DomainError:
                continue
            if collection == "artifacts" and field == "requires_review" and actor.kind != "human":
                continue
            if collection == "actors" and field == "owner_id" and item.kind != "agent":
                continue
            if collection == "tasks":
                if (
                    item.kind == "artifact_review"
                    and field == "status"
                    and actor.role not in {"owner", "coordinator"}
                ):
                    continue
                if field == "requested_assignee_id" and actor.role not in {"owner", "coordinator"}:
                    if item.created_by_id != actor.id:
                        continue
            allowed.append(field)
        return {
            "allowed": bool(allowed),
            "fields": allowed,
            "expected_version": item.version,
            "can_override": collection == "tasks" and actor.role in {"owner", "coordinator"},
        }

    def editable(self, db, project, actor, collection, resource_id):
        if collection == "projects":
            if resource_id != project.id:
                fail(404, "not_found", "Project not found")
            return project
        return self.get(db, MODELS[collection], project.id, resource_id)

    @staticmethod
    def edit_permission(actor, item, collection, changes):
        lead = actor.role in {"owner", "coordinator"}
        if collection == "messages":
            allowed = actor.id == item.sender_id and actor.role != "observer"
        elif collection == "actors":
            allowed = actor.role == "owner" or (
                actor.id == item.id and set(changes) <= {"name", "capabilities", "notes"}
            )
        elif collection == "activities":
            allowed = actor.id == item.actor_id
        elif actor.role == "observer":
            allowed = False
        elif collection in {"projects", "verifications"}:
            allowed = lead
        elif collection == "challenges":
            allowed = True
        elif collection == "tasks":
            allowed = lead or actor.id in {item.created_by_id, item.assignee_id}
        else:
            allowed = lead or item.author_id == actor.id
        if not allowed:
            fail(
                403, "edit_forbidden", "Only the author/assignee or permitted coordinator can edit"
            )

    def _revision(self, db, project, actor, collection, item, before, reason=""):
        after = record(item)
        revision = self.insert(
            db,
            project,
            m.Revision,
            collection=collection,
            resource_id=item.id,
            editor_id=actor.id,
            before=before,
            after=after,
            reason=reason,
            **(
                {m.REVISION_TARGETS[collection]: item.id}
                if collection in m.REVISION_TARGETS
                else {}
            ),
        )
        item._revision_id = revision.id
        return revision

    def edit_resource(self, db, project, actor, data, resource_id):
        collection = data.collection
        if data.force and (collection != "tasks" or actor.role not in {"owner", "coordinator"}):
            fail(403, "override_forbidden", "Only a coordinator can override a task assignment")
        item = self.editable(db, project, actor, collection, resource_id)
        self.edit_permission(actor, item, collection, data.changes)
        self.check_version(item, data.expected_version)
        changes = FIELDS[collection].model_validate(data.changes).model_dump(exclude_unset=True)
        changes = {
            key: value
            for key, value in changes.items()
            if key == "ttl_seconds" or getattr(item, key) != value
        }
        if not changes:
            fail(422, "no_changes", "No fields changed")
        before = record(item)
        if collection == "actors":
            self._edit_actor(db, project, actor, item, changes)
        elif collection == "tasks":
            self._edit_task(db, project, actor, item, changes, data.reason, data.force)
        elif collection == "findings":
            values = {**record(item), **changes}
            for field, model in [
                ("challenge_id", m.Challenge),
                ("task_id", m.Task),
                ("artifact_id", m.Artifact),
            ]:
                if values[field]:
                    self.get(db, model, project.id, values[field])
            if values["task_id"] and values["challenge_id"]:
                task = self.get(db, m.Task, project.id, values["task_id"])
                if task.challenge_id != values["challenge_id"]:
                    fail(422, "scope_mismatch", "Finding challenge must match its task")
        elif collection == "flags":
            self._edit_flag(db, project, item, changes)
        elif collection == "messages":
            from team_sync.attention import message_budget

            message_budget(db, project, actor, changes.get("priority", item.priority))
            if changes.get("task_id"):
                self.get(db, m.Task, project.id, changes["task_id"])
            item.fingerprint = None
        elif collection == "artifacts" and "requires_review" in changes:
            if actor.kind != "human":
                fail(
                    403, "human_action_required", "Only humans can change artifact approval policy"
                )
        elif collection == "activities":
            activity = self.report_activity(
                db,
                project,
                actor,
                s.ActivityReport(
                    state=changes.get("state", item.state),
                    detail=changes.get("detail", item.detail),
                    task_id=changes.get("task_id", item.task_id),
                    ttl_seconds=changes.get("ttl_seconds", 120),
                ),
                None,
            )
            self._revision(db, project, actor, collection, activity, before, data.reason)
            activity._skip_command_event = True
            return activity
        for key, value in changes.items():
            setattr(item, key, value)
        if item.version == before["version"]:
            item.version += 1
        if collection == "tasks" and "status" in changes:
            self._after_task(db, project, actor)
        if collection == "verifications":
            flag = self.get(db, m.Flag, project.id, item.flag_id)
            if flag.verification and flag.verification["id"] == item.id:
                old_flag = record(flag)
                flag.verification_status = item.status
                flag.verification = {
                    **flag.verification,
                    "method": item.method,
                    "evidence": item.evidence,
                    "revision": item.version,
                }
                flag.version += 1
                self._revision(db, project, actor, "flags", flag, old_flag, data.reason)
                self.emit(
                    db,
                    project,
                    actor,
                    "flag.update",
                    {"resource_id": flag.id, "version": flag.version},
                )
        self._revision(db, project, actor, collection, item, before, data.reason)
        item._event_type = f"{EVENT_NAMES[collection]}.update"
        return item

    def _edit_actor(self, db, project, actor, item, changes):
        if (
            item.role == "owner"
            and item.active
            and (
                changes.get("role", item.role) != "owner" or not changes.get("active", item.active)
            )
        ):
            owners = db.scalar(
                select(func.count())
                .select_from(m.Actor)
                .where(
                    m.Actor.project_id == project.id,
                    m.Actor.role == "owner",
                    m.Actor.active.is_(True),
                    m.Actor.id != item.id,
                )
            )
            if not owners:
                fail(409, "last_owner", "The last active owner cannot be demoted or deactivated")
        if "owner_id" in changes and changes["owner_id"]:
            owner = self.get(db, m.Actor, project.id, changes["owner_id"])
            if owner.kind != "human" or item.kind != "agent":
                fail(422, "invalid_owner", "Only an agent can refer to a human owner")
        if "name" in changes:
            changes["name"] = self.unique_name(db, project.id, changes["name"], item.kind, item.id)

    def _edit_task(self, db, project, actor, item, changes, reason, force=False):
        controls = set(changes) & {"status", "requested_assignee_id"}
        timestamp = db.scalar(select(func.clock_timestamp()))
        live = item.status == "running" and item.lease_until and item.lease_until > timestamp
        if (
            controls
            and live
            and (item.assignee_id != actor.id or "requested_assignee_id" in changes)
        ):
            if not force:
                fail(
                    409,
                    "task_in_progress",
                    "A live assignment needs an explicit coordinator override",
                )
            if not reason.strip():
                fail(
                    422,
                    "override_reason_required",
                    "Explain why the active assignment is being changed",
                )
            old = self.get(db, m.Actor, project.id, item.assignee_id)
            item.status, item.assignee_id, item.lease_until = "open", None, None
            if item.kind == "artifact_review":
                item.approval_status, item.approved_by_id, item.analysis_started_at = (
                    "pending",
                    None,
                    None,
                )
                item.approval_note = None
            self._after_task(db, project, old, caller=actor)
            if old.id != actor.id and old.active:
                self._notice(
                    db,
                    project,
                    actor,
                    old.id,
                    f"{actor.name} змінив призначення «{item.title}». {reason}",
                    item,
                    "status",
                    control=True,
                )
            self.emit(
                db,
                project,
                actor,
                "task.override",
                {"resource_id": item.id, "previous_assignee_id": old.id, "resource_type": "tasks"},
            )
        if changes.get("status", item.status) == "done" and not changes.get("result", item.result):
            fail(422, "result_required", "A completed task requires a result")
        if "challenge_id" in changes:
            if changes["challenge_id"]:
                self.get(db, m.Challenge, project.id, changes["challenge_id"])
            for finding in db.scalars(
                select(m.Finding).where(
                    m.Finding.project_id == project.id, m.Finding.task_id == item.id
                )
            ):
                before = record(finding)
                finding.challenge_id = changes["challenge_id"]
                finding.version += 1
                self._revision(
                    db,
                    project,
                    actor,
                    "findings",
                    finding,
                    before,
                    reason or "Task moved to another challenge",
                )
                self.emit(
                    db,
                    project,
                    actor,
                    "finding.update",
                    {"resource_id": finding.id, "version": finding.version},
                )
        if "requested_assignee_id" in changes:
            self.assign_task(
                db,
                project,
                actor,
                s.Assign(
                    expected_version=item.version, recipient_id=changes["requested_assignee_id"]
                ),
                item.id,
            )
        if "status" in changes:
            if item.kind == "artifact_review" and changes["status"] != "open":
                fail(
                    409,
                    "review_workflow_required",
                    "Use review decisions/start/report for analysis",
                )
            timestamp = db.scalar(select(func.clock_timestamp()))
            if item.status == "running" and item.lease_until and item.lease_until > timestamp:
                if item.assignee_id != actor.id:
                    fail(
                        409,
                        "task_in_progress",
                        "The assignee must finish or release its live lease",
                    )
            if changes["status"] == "done" and not (changes.get("result") or item.result):
                fail(422, "result_required", "A completed task requires a result")
            item.lease_until = None
            if changes["status"] == "open":
                item.assignee_id = None
                item.result = None
                if item.kind == "artifact_review":
                    item.approval_status, item.approved_by_id, item.analysis_started_at = (
                        "pending",
                        None,
                        None,
                    )
                    item.approval_note = None

    def _edit_flag(self, db, project, item, changes):
        if changes.get("challenge_id"):
            self.get(db, m.Challenge, project.id, changes["challenge_id"])
        if set(changes) & {"value", "challenge_id", "target", "round"}:
            values = {**flag_subject(item), **changes}
            scoped_hash = digest(
                json.dumps(
                    [values[k] for k in ("value", "challenge_id", "target", "round")],
                    ensure_ascii=False,
                )
            )
            exists = db.scalar(
                select(m.Flag.id).where(
                    m.Flag.project_id == project.id,
                    m.Flag.scope_hash == scoped_hash,
                    m.Flag.id != item.id,
                )
            )
            if exists:
                fail(409, "duplicate_flag", f"Flag already recorded as {exists}")
            item.scope_hash = scoped_hash
            item.verification_status, item.verification = "unverified", None

    def edit_history(
        self,
        project_id,
        token,
        collection,
        resource_id,
        offset=0,
        limit=10,
        revision_id=None,
        max_chars=12000,
    ):
        with self.context(project_id, token) as (db, project, actor):
            item = self.editable(db, project, actor, collection, resource_id)
            if collection == "messages" and actor.id not in (item.sender_id, item.recipient_id):
                if item.recipient_id is not None:
                    fail(404, "not_found", "Message not found")
            stmt = select(m.Revision).where(
                m.Revision.project_id == project.id,
                m.Revision.collection == collection,
                m.Revision.resource_id == resource_id,
            )
            if revision_id:
                revision = db.scalar(stmt.where(m.Revision.id == revision_id))
                if revision is None:
                    fail(404, "not_found", "Revision not found")
                return record(revision)
            total = db.scalar(select(func.count()).select_from(stmt.subquery()))
            revisions = list(
                db.scalars(
                    stmt.order_by(m.Revision.created_at.desc(), m.Revision.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            items = []
            for revision in revisions:
                value = record(revision)
                value["reason"] = revision.reason[:500] + (
                    "…" if len(revision.reason) > 500 else ""
                )
                changed = [
                    key
                    for key in revision.after
                    if key != "version" and revision.before.get(key) != revision.after.get(key)
                ]
                value["changed_fields"] = changed
                for side in ("before", "after"):

                    def excerpt(entry):
                        if isinstance(entry, str):
                            return entry[:300] + "…" if len(entry) > 300 else entry
                        if isinstance(entry, dict):
                            return {key: excerpt(value) for key, value in list(entry.items())[:20]}
                        if isinstance(entry, list):
                            return [excerpt(value) for value in entry[:10]]
                        return entry

                    value[side] = {
                        key: excerpt(entry)
                        for key, entry in value[side].items()
                        if key in changed or key == "version"
                    }
                items.append(value)
            result = {
                "items": items,
                "total": total,
                "has_more": offset + len(items) < total,
                "excerpts": True,
            }
            while items and len(json.dumps(result, ensure_ascii=False, indent=2)) > max_chars:
                if len(items) == 1:
                    items[0]["before"] = {"version": revisions[0].before["version"]}
                    items[0]["after"] = {"version": revisions[0].after["version"]}
                    break
                items.pop()
            result["has_more"] = offset + len(items) < total
            return result
