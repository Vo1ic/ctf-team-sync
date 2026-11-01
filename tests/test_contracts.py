import json
from pathlib import Path

from team_sync.contracts import operation_schemas
from team_sync.schemas import EventEnvelope


def test_checked_in_contracts_are_current():
    directory = Path(__file__).parents[1] / "contracts"
    assert json.loads((directory / "commands.schema.json").read_text()) == operation_schemas()
    assert (
        json.loads((directory / "event.schema.json").read_text())
        == EventEnvelope.model_json_schema()
    )
