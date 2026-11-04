import json
from pathlib import Path

from team_sync import schemas as s
from team_sync.bridge_protocol import DeliveryBatch, request_schema


def operation_schemas():
    models = {
        "challenge.create": s.ChallengeCreate,
        "task.create": s.TaskCreate,
        "task.claim": s.Lease,
        "task.renew": s.Lease,
        "task.complete": s.Complete,
        "task.release": s.Release,
        "task.assign": s.Assign,
        "actor.rename": s.Rename,
        "activity.report": s.ActivityReport,
        "artifact.review.request": s.ReviewRequest,
        "artifact.review.decide": s.ReviewDecision,
        "artifact.review.start": s.Versioned,
        "artifact.review.complete": s.ReviewFinish,
        "artifact.archive": s.Archive,
        "finding.create": s.FindingCreate,
        "flag.create": s.FlagCreate,
        "flag.verify": s.FlagVerify,
        "submission.create": s.SubmissionCreate,
        "message.send": s.MessageCreate,
        "message.ack": s.MessageAck,
    }
    return {name: model.model_json_schema() for name, model in models.items()}


def main():
    # The API schema factory constructs an engine but never opens a DB connection.
    from team_sync.api import create_app
    from team_sync.settings import Settings

    directory = Path("contracts")
    directory.mkdir(exist_ok=True)
    app = create_app(
        Settings(
            "postgresql+psycopg://unused:unused@localhost/unused",
            "contract-export-only",
            Path("data/artifacts"),
        )
    )
    documents = {
        "openapi.json": app.openapi(),
        "commands.schema.json": operation_schemas(),
        "event.schema.json": s.EventEnvelope.model_json_schema(),
        "agent-bridge.schema.json": request_schema(),
        "delivery-batch.schema.json": DeliveryBatch.model_json_schema(),
    }
    for name, document in documents.items():
        (directory / name).write_text(json.dumps(document, indent=2) + "\n")
    app.state.core.engine.dispose()
