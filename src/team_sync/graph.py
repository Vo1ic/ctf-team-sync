"""The team's knowledge graph: hypothesis chains and file attachments as typed edges.

Edges never delete or rewrite their endpoints. A refutation changes the state of a
hypothesis through a normal revision, so the change keeps its provenance.
"""

from sqlalchemy import select

from team_sync import models as m
from team_sync.domain import fail, record

NODE_MODELS = {
    "challenge": m.Challenge,
    "task": m.Task,
    "finding": m.Finding,
    "flag": m.Flag,
    "artifact": m.Artifact,
}
ALLOWED = {
    "attached_to": ({"artifact"}, {"challenge", "task", "finding"}),
    "leads_to": ({"finding", "task"}, {"finding", "flag", "task"}),
    "supports": ({"finding"}, {"finding"}),
    "refutes": ({"finding"}, {"finding", "flag"}),
    "supersedes": ({"finding"}, {"finding"}),
}


class GraphOperations:
    def _link(
        self, db, project, actor, source_type, source_id, target_type, target_id, relation, note=""
    ):
        sources, targets = ALLOWED[relation]
        if source_type not in sources or target_type not in targets:
            fail(
                422,
                "invalid_link",
                f"{relation} links {'/'.join(sorted(sources))} -> {'/'.join(sorted(targets))}",
            )
        if source_id == target_id:
            fail(422, "invalid_link", "A record cannot link to itself")
        self.get(db, NODE_MODELS[source_type], project.id, source_id)
        target = self.get(db, NODE_MODELS[target_type], project.id, target_id)
        existing = db.scalar(
            select(m.Link).where(
                m.Link.project_id == project.id,
                m.Link.source_id == source_id,
                m.Link.target_id == target_id,
                m.Link.relation == relation,
            )
        )
        if existing:
            return existing, False
        link = self.insert(
            db,
            project,
            m.Link,
            author_id=actor.id,
            relation=relation,
            note=note[:500],
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            **{f"source_{source_type}_id": source_id, f"target_{target_type}_id": target_id},
        )
        if isinstance(target, m.Finding) and target.kind == "hypothesis":
            source = self.get(db, NODE_MODELS[source_type], project.id, source_id)
            state = {"refutes": "refuted", "supports": "confirmed"}.get(relation)
            if relation == "supports" and getattr(source, "kind", None) != "result":
                state = None  # only a result confirms; observations merely support
            if state and target.status != state:
                before = record(target)
                target.status = state
                target.version += 1
                self._revision(
                    db, project, actor, "findings", target, before, f"{relation} by {source_id}"
                )
                self.emit(
                    db,
                    project,
                    actor,
                    "finding.update",
                    {"resource_id": target.id, "version": target.version, "status": state},
                )
        return link, True

    def create_link(self, db, project, actor, data, resource_id):
        link, created = self._link(
            db,
            project,
            actor,
            data.source_type,
            data.source_id,
            data.target_type,
            data.target_id,
            data.relation,
            data.note,
        )
        link._event_extra = {
            "relation": link.relation,
            "source_id": link.source_id,
            "target_id": link.target_id,
        }
        if not created:
            link._skip_command_event = True  # idempotent: the edge already existed
        return link

    def delete_link(self, db, project, actor, data, resource_id):
        link = self.get(db, m.Link, project.id, resource_id)
        if actor.id != link.author_id and actor.role not in {"owner", "coordinator"}:
            fail(403, "forbidden", "Only the link author or a coordinator can remove it")
        value = record(link)
        db.delete(link)
        db.flush()
        return {
            "resource_type": "links",
            "id": value["id"],
            "deleted": True,
            "relation": value["relation"],
            "source_id": value["source_id"],
            "target_id": value["target_id"],
            "_event_type": "link.delete",
        }

    def chain_finding(self, db, project, actor, finding, based_on, refutes):
        """Edges requested together with a new finding (one command, one transaction)."""
        for relation, ids, outgoing in (("leads_to", based_on, False), ("refutes", refutes, True)):
            for other in dict.fromkeys(ids):
                if outgoing:
                    args = ("finding", finding.id, "finding", other)
                else:
                    args = ("finding", other, "finding", finding.id)
                link, created = self._link(db, project, actor, *args, relation)
                if created:
                    self.emit(
                        db,
                        project,
                        actor,
                        "link.create",
                        {
                            "resource_id": link.id,
                            "resource_type": "links",
                            "relation": relation,
                            "source_id": link.source_id,
                            "target_id": link.target_id,
                        },
                    )
