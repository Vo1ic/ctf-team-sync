"""Challenge dossier: one complete Markdown document (writeup + full solving history).

Generated on read from current records, so it is always up to date. The editable part
is ``challenge.writeup``; everything else (files, tasks, hypothesis graph, findings,
flags) comes from shared state with authors and timestamps.
"""

import re
from datetime import datetime

from sqlalchemy import or_, select

from team_sync import models as m
from team_sync.onboarding import participants

KINDS = {
    "observation": "Спостереження",
    "hypothesis": "Гіпотеза",
    "result": "Результат",
    "failed_attempt": "Невдала спроба",
    "decoy": "Приманка",
}
HINT_STATES = {
    "pending": "чекає рішення",
    "approved": "дозволено",
    "declined": "відхилено",
    "used": "використано",
}
STATUSES = {"open": "відкрита", "confirmed": "підтверджена", "refuted": "спростована"}
FLAG_STATES = {"unverified": "не перевірено", "confirmed": "підтверджено", "rejected": "відхилено"}
TASK_STATES = {
    "open": "вільна",
    "running": "в роботі",
    "review": "чекає підтвердження флага",
    "done": "зарахована",
}
RELATIONS = {
    "leads_to": "веде до",
    "supports": "підтримує",
    "refutes": "спростовує",
    "supersedes": "заміняє",
    "attached_to": "прикріплено до",
}
SIGNED = re.compile(r"([?&](?:token|signature|sig|access_token)=)[^&\s)]+", re.I)


def when(value):
    return value.strftime("%d.%m %H:%M") if isinstance(value, datetime) else "—"


def fence(text):
    """A code fence that cannot be closed by the content itself."""
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}\n{text.rstrip()}\n{ticks}"


def mermaid_label(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = text if len(text) <= 58 else text[:57] + "…"
    return text.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def size(value):
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return str(value)


def node_class(node):
    if node["type"] == "finding":
        if node["kind"] == "hypothesis":
            return f"hyp_{node['status'] or 'open'}"
        return node["kind"]
    if node["type"] == "flag":
        return f"flag_{node['status']}"
    return node["type"]


def collect(db, project, challenge):
    tasks = list(
        db.scalars(
            select(m.Task)
            .where(m.Task.project_id == project.id, m.Task.challenge_id == challenge.id)
            .order_by(m.Task.created_at, m.Task.id)
        )
    )
    task_ids = [task.id for task in tasks]
    scope = or_(m.Finding.challenge_id == challenge.id, m.Finding.task_id.in_(task_ids or [""]))
    findings = list(
        db.scalars(
            select(m.Finding)
            .where(m.Finding.project_id == project.id, scope)
            .order_by(m.Finding.created_at, m.Finding.id)
        )
    )
    flags = list(
        db.scalars(
            select(m.Flag)
            .where(
                m.Flag.project_id == project.id,
                or_(m.Flag.challenge_id == challenge.id, m.Flag.task_id.in_(task_ids or [""])),
            )
            .order_by(m.Flag.created_at, m.Flag.id)
        )
    )
    ids = {challenge.id, *task_ids, *(f.id for f in findings), *(f.id for f in flags)}
    links = list(
        db.scalars(
            select(m.Link)
            .where(
                m.Link.project_id == project.id,
                or_(m.Link.source_id.in_(ids), m.Link.target_id.in_(ids)),
            )
            .order_by(m.Link.created_at, m.Link.id)
        )
    )
    artifact_ids = {link.source_id for link in links if link.relation == "attached_to"}
    artifact_ids |= {f.artifact_id for f in findings if f.artifact_id}
    artifacts = (
        list(
            db.scalars(
                select(m.Artifact)
                .where(m.Artifact.project_id == project.id, m.Artifact.id.in_(artifact_ids))
                .order_by(m.Artifact.created_at)
            )
        )
        if artifact_ids
        else []
    )
    submissions = {}
    if flags:
        for item in db.scalars(
            select(m.Submission)
            .where(
                m.Submission.project_id == project.id,
                m.Submission.flag_id.in_([f.id for f in flags]),
            )
            .order_by(m.Submission.created_at)
        ):
            submissions[item.flag_id] = item
    actors = {
        actor.id: actor.name
        for actor in db.scalars(select(m.Actor).where(m.Actor.project_id == project.id))
    }
    hints = list(
        db.scalars(
            select(m.HintRequest)
            .where(
                m.HintRequest.project_id == project.id, m.HintRequest.challenge_id == challenge.id
            )
            .order_by(m.HintRequest.created_at)
        )
    )
    return tasks, findings, flags, links, artifacts, submissions, actors, hints


def build(db, project, challenge):
    tasks, findings, flags, links, artifacts, submissions, actors, hints = collect(
        db, project, challenge
    )
    task_titles = {task.id: task.title for task in tasks}
    number = {finding.id: index for index, finding in enumerate(findings, 1)}
    flag_number = {flag.id: index for index, flag in enumerate(flags, 1)}

    def ref(kind, ident):
        if kind == "finding" and ident in number:
            return f"#{number[ident]}"
        if kind == "flag" and ident in flag_number:
            return f"флаг F{flag_number[ident]}"
        if kind == "task" and ident in task_titles:
            return f"робота «{task_titles[ident]}»"
        if kind == "challenge":
            return "завдання"
        return f"{kind}:{ident[:8]}"

    # ----- graph -------------------------------------------------------------------------
    nodes, edges = {}, []
    for finding in findings:
        nodes[finding.id] = {
            "id": finding.id,
            "type": "finding",
            "kind": finding.kind,
            "status": finding.status,
            "label": finding.title,
            "ref": f"#{number[finding.id]}",
            "author": actors.get(finding.author_id),
            "task": task_titles.get(finding.task_id),
        }
    for flag in flags:
        nodes[flag.id] = {
            "id": flag.id,
            "type": "flag",
            "kind": "flag",
            "status": flag.verification_status,
            "label": flag.value,
            "ref": f"F{flag_number[flag.id]}",
            "author": actors.get(flag.author_id),
            "task": task_titles.get(flag.task_id),
        }
    artifact_names = {artifact.id: artifact.name for artifact in artifacts}
    for finding in findings:
        files = [
            artifact_names[link.source_id]
            for link in links
            if link.relation == "attached_to"
            and link.target_id == finding.id
            and link.source_id in artifact_names
        ]
        if finding.artifact_id in artifact_names:
            files.append(artifact_names[finding.artifact_id])
        nodes[finding.id]["files"] = files
    for link in links:
        if link.relation == "attached_to":
            continue  # files are listed and badged on their finding, not drawn as nodes
        for ident, kind in ((link.source_id, link.source_type), (link.target_id, link.target_type)):
            if ident not in nodes and kind in {"task", "artifact"}:
                label = task_titles.get(ident) or artifact_names.get(ident) or kind
                nodes[ident] = {
                    "id": ident,
                    "type": kind,
                    "kind": kind,
                    "status": None,
                    "label": label,
                    "ref": kind,
                }
        if link.source_id in nodes and link.target_id in nodes:
            edges.append(
                {
                    "id": link.id,
                    "source": link.source_id,
                    "target": link.target_id,
                    "relation": link.relation,
                    "note": link.note,
                }
            )
    # Flags without explicit edges hang under the latest result of the same task.
    linked_targets = {edge["target"] for edge in edges}
    for flag in flags:
        if flag.id in linked_targets:
            continue
        origin = next(
            (
                f
                for f in reversed(findings)
                if f.kind == "result" and (f.task_id == flag.task_id or not flag.task_id)
            ),
            None,
        )
        if origin:
            edges.append(
                {
                    "id": f"implicit-{flag.id}",
                    "source": origin.id,
                    "target": flag.id,
                    "relation": "leads_to",
                    "note": "implicit",
                }
            )

    # ----- markdown ----------------------------------------------------------------------
    confirmed = [f for f in flags if f.verification_status == "confirmed"]
    if confirmed:
        # Solved means a human confirmed a correct flag; nothing else counts.
        state = "✅ розв'язано (флаг підтверджено)"
    elif any(task.status == "review" for task in tasks) or any(
        f.verification_status == "unverified" for f in flags
    ):
        state = "🕵️ флаг на перевірці"
    elif any(task.status == "running" for task in tasks) or findings:
        state = "🔄 в роботі"
    else:
        state = "⏳ не розпочато"
    lines = [f"# {challenge.title}", ""]
    meta = [
        f"**Категорія:** {challenge.category or '—'}",
        f"**Бали:** {challenge.points if challenge.points is not None else '—'}",
        f"**Статус:** {state}",
    ]
    lines.append(" · ".join(meta))
    if challenge.source_url:
        source = SIGNED.sub(r"\1<redacted>", challenge.source_url)
        lines += ["", f"**Платформа:** {source}"]
    hypotheses = [f for f in findings if f.kind == "hypothesis"]
    lines += [
        "",
        f"Знахідок: {len(findings)} · гіпотез: {len(hypotheses)} "
        f"(спростовано {sum(f.status == 'refuted' for f in hypotheses)}) · "
        f"флагів: {len(flags)} · файлів: {len(artifacts)} · робіт: {len(tasks)}",
    ]
    decoys = [f for f in findings if f.kind == "decoy"]
    if decoys:
        lines += ["", "## ⚠️ Відомі приманки", ""]
        lines += [f"- #{number[f.id]} {f.title}" for f in decoys]
    lines += ["", "## Райтап", ""]
    lines.append(
        challenge.writeup.strip()
        or "_Райтап ще не написано. Нижче — повна історія розв'язання з доказами._"
    )
    lines += ["", "## Умова", "", SIGNED.sub(r"\1<redacted>", challenge.description or "—")]

    lines += ["", "## Файли", ""]
    if artifacts:
        targets = {}
        for link in links:
            if link.relation == "attached_to":
                targets.setdefault(link.source_id, []).append(ref(link.target_type, link.target_id))
        for artifact in artifacts:
            where = ", ".join(targets.get(artifact.id, [])) or "знахідка"
            lines.append(
                f"- `{artifact.name}` · {size(artifact.size)} · sha256 `{artifact.sha256[:16]}…` "
                f"· `artifact:{artifact.id}` · {where}"
            )
    else:
        lines.append("_Файлів не прикріплено._")

    lines += ["", "## Роботи", ""]
    joined = participants(db, project.id, [task.id for task in tasks])
    for task in tasks:
        people = ", ".join(p["name"] for p in joined.get(task.id, []))
        lines.append(
            f"- **{task.title}** — {TASK_STATES.get(task.status, task.status)}"
            + (f" · виконавець: {actors.get(task.assignee_id)}" if task.assignee_id else "")
            + (f" · разом: {people}" if people else "")
        )
        if task.result:
            # Indent every line so a multi-line result stays inside its list item.
            result = "\n".join(
                f"    {line}" if line.strip() else "" for line in task.result.strip().splitlines()
            )
            lines.append(f"  - Результат:\n{result}")
    if not tasks:
        lines.append("_Робіт немає._")

    lines += ["", "## Граф гіпотез", ""]
    if nodes:
        aliases = {ident: f"n{index}" for index, ident in enumerate(nodes, 1)}
        graph = ["flowchart TD"]
        for ident, node in nodes.items():
            prefix = {
                "finding": f"{node['ref']} {KINDS.get(node['kind'], node['kind'])}",
                "flag": f"{node['ref']} 🚩",
                "task": "Робота",
                "artifact": "Файл",
            }[node["type"]]
            shape = ('{{"', '"}}') if node["type"] == "flag" else ('["', '"]')
            graph.append(
                f"  {aliases[ident]}{shape[0]}{mermaid_label(prefix + ': ' + node['label'])}"
                f"{shape[1]}:::{node_class(node)}"
            )
        arrows = {
            "leads_to": "-->",
            "supports": "-. підтримує .->",
            "refutes": "-- спростовує --x",
            "supersedes": "-. заміняє .->",
            "attached_to": "-.-",
        }
        for edge in edges:
            graph.append(
                f"  {aliases[edge['source']]} {arrows[edge['relation']]} {aliases[edge['target']]}"
            )
        graph += [
            "  classDef hyp_open fill:#fff4ce,stroke:#b8860b,color:#222",
            "  classDef hyp_confirmed fill:#d7f5dd,stroke:#2e7d32,color:#222",
            "  classDef hyp_refuted fill:#fde2e1,stroke:#c62828,color:#666,stroke-dasharray:4 3",
            "  classDef result fill:#dcecff,stroke:#1565c0,color:#222",
            "  classDef observation fill:#eeeeee,stroke:#777,color:#222",
            "  classDef failed_attempt fill:#f3e5f5,stroke:#8e24aa,color:#555",
            "  classDef decoy fill:#fff3e0,stroke:#ef6c00,color:#555,stroke-dasharray:2 2",
            "  classDef flag_confirmed fill:#2e7d32,stroke:#1b5e20,color:#fff",
            "  classDef flag_rejected fill:#c62828,stroke:#8e0000,color:#fff",
            "  classDef flag_unverified fill:#fff,stroke:#b8860b,color:#222",
            "  classDef task fill:#fff,stroke:#555,color:#222",
            "  classDef artifact fill:#fff,stroke:#555,color:#222",
        ]
        lines.append(fence("\n".join(graph)).replace("```\n", "```mermaid\n", 1))
        lines += [
            "",
            "Легенда: жовте — відкрита гіпотеза, зелене — підтверджене, червоне пунктиром — "
            "спростоване, синє — результат, фіолетове — невдала спроба, шестикутник — флаг.",
        ]
    else:
        lines.append("_Знахідок ще немає._")

    lines += ["", "## Хід розв'язання", ""]
    incoming, outgoing = {}, {}
    for edge in edges:
        if edge["note"] == "implicit":
            continue
        outgoing.setdefault(edge["source"], []).append(edge)
        incoming.setdefault(edge["target"], []).append(edge)
    for finding in findings:
        title = KINDS.get(finding.kind, finding.kind)
        if finding.kind == "hypothesis":
            title += f" · {STATUSES.get(finding.status or 'open')}"
        strike = "~~" if finding.status == "refuted" else ""
        lines.append(f"### #{number[finding.id]} [{title}] {strike}{finding.title}{strike}")
        lines.append(
            f"_{actors.get(finding.author_id, '?')} · {when(finding.created_at)}"
            + (f" · {task_titles[finding.task_id]}" if finding.task_id in task_titles else "")
            + "_"
        )
        relations = []
        for edge in incoming.get(finding.id, []):
            kind = nodes.get(edge["source"], {}).get("type", "finding")
            if edge["relation"] == "leads_to":
                relations.append(f"випливає з {ref(kind, edge['source'])}")
            else:
                relations.append(f"{ref(kind, edge['source'])} {RELATIONS[edge['relation']]} це")
        for edge in outgoing.get(finding.id, []):
            kind = nodes.get(edge["target"], {}).get("type", "finding")
            relations.append(f"{RELATIONS[edge['relation']]} {ref(kind, edge['target'])}")
        if relations:
            lines.append("")
            lines.append("Зв'язки: " + "; ".join(relations))
        lines += ["", finding.body.strip(), "", "**Докази:**", "", fence(finding.evidence), ""]
    if not findings:
        lines.append("_Ще нічого не опубліковано._")

    if hints:
        lines += ["", "## Підказки платформи", ""]
        spent = sum(h.cost or 0 for h in hints if h.status == "used")
        for hint in hints:
            lines.append(
                f"- {HINT_STATES[hint.status]} · {hint.hint_ref or 'підказка'}"
                + (f" · {hint.cost} балів" if hint.cost is not None else "")
                + f" · просив {actors.get(hint.requester_id, '?')}"
                + (f", вирішив {actors.get(hint.decided_by_id)}" if hint.decided_by_id else "")
                + f": {hint.reason.strip()[:300]}"
            )
        lines.append(f"- Витрачено: {spent} балів")
    lines += ["", "## Флаги", ""]
    for flag in flags:
        verdict = FLAG_STATES.get(flag.verification_status, flag.verification_status)
        submission = submissions.get(flag.id)
        lines.append(
            f"- F{flag_number[flag.id]} `{flag.value}` — {verdict}"
            + (f" · платформа: {submission.outcome}" if submission else "")
            + f" · {actors.get(flag.author_id, '?')}, {when(flag.created_at)}"
            + (f" · {task_titles[flag.task_id]}" if flag.task_id in task_titles else "")
        )
    if not flags:
        lines.append("_Флагів немає._")
    lines += ["", "---", f"_Згенеровано Team Sync · {project.name}_", ""]
    return {
        "challenge": {
            "id": challenge.id,
            "title": challenge.title,
            "category": challenge.category,
            "points": challenge.points,
            "version": challenge.version,
            "state": state,
        },
        "markdown": "\n".join(lines),
        "graph": {"nodes": list(nodes.values()), "edges": edges},
    }
