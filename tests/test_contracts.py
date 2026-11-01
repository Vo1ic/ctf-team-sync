import json
from pathlib import Path

from team_sync.bridge_protocol import DeliveryBatch, request_schema
from team_sync.contracts import operation_schemas
from team_sync.schemas import EventEnvelope


def test_checked_in_contracts_are_current():
    directory = Path(__file__).parents[1] / "contracts"
    assert json.loads((directory / "commands.schema.json").read_text()) == operation_schemas()
    assert (
        json.loads((directory / "event.schema.json").read_text())
        == EventEnvelope.model_json_schema()
    )
    assert json.loads((directory / "agent-bridge.schema.json").read_text()) == request_schema()
    assert (
        json.loads((directory / "delivery-batch.schema.json").read_text())
        == DeliveryBatch.model_json_schema()
    )
