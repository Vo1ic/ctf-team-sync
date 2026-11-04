"""Versioned wire contracts for runtimes with no MCP or lifecycle hooks."""

from functools import reduce
from operator import or_
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, create_model

from team_sync.schemas import (
    ID,
    AgentAction,
    AttentionQuery,
    Input,
    ResourceCollection,
    SearchQuery,
    WorkspaceQuery,
)

Consumer = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
RequestID = Annotated[str, Field(min_length=1, max_length=128)] | StrictInt | None


class ClientInfo(Input):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[str, Field(max_length=100)] | None = None
    provider: Annotated[str, Field(max_length=200)] | None = None
    model: Annotated[str, Field(max_length=200)] | None = None


class Hello(Input):
    protocol_versions: list[StrictInt] = Field(min_length=1, max_length=20)
    client: ClientInfo


class Sync(Input):
    timeout: int = Field(default=0, ge=0, le=25)


class ConsumerParams(Input):
    consumer: Consumer


class ReadContext(ConsumerParams):
    max_items: int = Field(default=12, ge=1, le=50)
    max_chars: int = Field(default=6000, ge=1024, le=20000)


class AckContext(ConsumerParams):
    delivery_id: ID


class ListItems(Input):
    collection: Literal[
        "actors",
        "challenges",
        "tasks",
        "findings",
        "flags",
        "verifications",
        "submissions",
        "artifacts",
        "activities",
    ]
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class Inbox(Input):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class GetResource(Input):
    collection: ResourceCollection
    resource_id: ID
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=50)


class History(GetResource):
    limit: int = Field(default=5, ge=1, le=20)
    revision_id: ID | None = None
    max_chars: int = Field(default=12000, ge=4000, le=32000)


class EditContract(Input):
    collection: ResourceCollection


class RPCEnvelope(Input):
    jsonrpc: Literal["2.0"]
    id: RequestID = None
    method: str
    params: dict = Field(default_factory=dict)


METHODS = {
    "protocol/hello": Hello,
    "events/sync": Sync,
    "context/read": ReadContext,
    "context/ack": AckContext,
    "commands/execute": AgentAction,
    "collections/list": ListItems,
    "messages/inbox": Inbox,
    "messages/outbox": Inbox,
    "messages/attention": AttentionQuery,
    "workspace/context": WorkspaceQuery,
    "resources/get": GetResource,
    "resources/history": History,
    "resources/edit-contract": EditContract,
    "resources/search": SearchQuery,
}


class DeliveryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    project_id: ID
    actor_id: ID | None
    consumer: Consumer
    delivery_id: ID | None
    after_cursor: int = Field(ge=0)
    through_cursor: int = Field(ge=0)
    items: list[dict]


def request_schema():
    variants = tuple(
        create_model(
            method.replace("/", "_") + "Request",
            __base__=RPCEnvelope,
            method=(Literal[method], ...),
            params=(schema, ...),
        )
        for method, schema in METHODS.items()
    )
    return TypeAdapter(
        Annotated[reduce(or_, variants), Field(discriminator="method")]
    ).json_schema()
