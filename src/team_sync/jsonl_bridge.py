"""JSON-RPC over newline-delimited JSON for arbitrary agent runtimes."""

import json
import sqlite3
import sys

import httpx
from pydantic import ValidationError

from team_sync.bridge_protocol import METHODS, RPCEnvelope
from team_sync.delivery import DeliveryError, Receiver, RemoteError
from team_sync.host_adapters import capabilities

MAX_FRAME_CHARS = 2 * 1024 * 1024


class RPCError(Exception):
    def __init__(self, code, message, data=None):
        self.code, self.message, self.data = code, message, data


class JSONLBridge:
    def __init__(self, settings, spool, *, transport=None):
        self.spool = spool
        self.receiver = Receiver(settings, spool, transport=transport)
        self.negotiated = False

    def close(self):
        self.receiver.close()

    def dispatch(self, method, params):
        if method == "protocol/hello":
            if 1 not in params.protocol_versions:
                raise RPCError(-32001, "No supported protocol version", {"supported_versions": [1]})
            self.negotiated = True
            return {
                "protocol_version": 1,
                "client": params.client.model_dump(exclude_none=True),
                "capabilities": capabilities("generic"),
                "max_batch_size": 50,
            }
        if not self.negotiated:
            raise RPCError(-32002, "Call protocol/hello before using the bridge")
        if method == "events/sync":
            count = self.receiver.poll(params.timeout)
            return {"events_received": count, **self.spool.status()}
        if method == "context/read":
            return self.spool.read_context(
                params.consumer, limit=params.max_items, max_chars=params.max_chars
            )
        if method == "context/ack":
            return self.spool.ack_context(params.consumer, params.delivery_id)
        if method == "commands/execute":
            return self.receiver.request("POST", "commands", json=params.model_dump(mode="json"))
        if method == "collections/list":
            return self.receiver.request(
                "GET",
                f"collections/{params.collection}",
                params={"offset": params.offset, "limit": params.limit},
            )
        if method == "messages/inbox":
            return self.receiver.request("GET", "inbox", params=params.model_dump())
        if method == "messages/outbox":
            return self.receiver.request("GET", "outbox", params=params.model_dump())
        if method == "messages/attention":
            return self.receiver.request("GET", "attention", params=params.model_dump())
        if method == "journal/read":
            return self.receiver.request(
                "GET", "journal", params=params.model_dump(exclude_none=True)
            )
        if method == "workspace/context":
            return self.receiver.request(
                "GET", "agent-context", params=params.model_dump(exclude_none=True)
            )
        if method == "resources/search":
            return self.receiver.request(
                "GET", "search", params=params.model_dump(exclude_none=True)
            )
        if method == "resources/get":
            return self.receiver.request(
                "GET",
                f"resources/{params.collection}/{params.resource_id}",
                params={"offset": params.offset, "limit": params.limit},
            )
        if method == "resources/history":
            return self.receiver.request(
                "GET",
                f"resources/{params.collection}/{params.resource_id}/history",
                params=params.model_dump(exclude={"collection", "resource_id"}, exclude_none=True),
            )
        if method == "resources/edit-contract":
            return self.receiver.request("GET", "edit-contracts", params=params.model_dump())
        raise RPCError(-32601, "Method not found")

    @staticmethod
    def error(request_id, code, message, data=None):
        value = {"code": code, "message": message}
        if data is not None:
            value["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": value}

    def handle(self, raw, *, allow_batch=True):
        if isinstance(raw, list):
            if not allow_batch or not raw or len(raw) > 50:
                return self.error(None, -32600, "Invalid request batch")
            replies = [
                reply
                for request in raw
                if (reply := self.handle(request, allow_batch=False)) is not None
            ]
            return replies or None
        notification = (
            isinstance(raw, dict)
            and raw.get("jsonrpc") == "2.0"
            and isinstance(raw.get("method"), str)
            and "id" not in raw
        )
        request_id = raw.get("id") if isinstance(raw, dict) else None
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int, type(None))):
            request_id = None
        if isinstance(request_id, str) and len(request_id) > 128:
            request_id = None
        try:
            request = RPCEnvelope.model_validate(raw)
        except ValidationError:
            return None if notification else self.error(request_id, -32600, "Invalid request")
        try:
            if request.method not in METHODS:
                raise RPCError(-32601, "Method not found")
            try:
                params = METHODS[request.method].model_validate(request.params)
            except ValidationError as error:
                raise RPCError(-32602, "Invalid parameters") from error
            result = self.dispatch(request.method, params)
            response = {"jsonrpc": "2.0", "id": request.id, "result": result}
        except RPCError as error:
            response = self.error(request.id, error.code, error.message, error.data)
        except RemoteError as error:
            response = self.error(
                request.id,
                -32010,
                "Remote request failed",
                {"status": error.status, "code": error.code},
            )
        except httpx.HTTPError:
            response = self.error(request.id, -32011, "Connection unavailable", {"retryable": True})
        except DeliveryError:
            response = self.error(request.id, -32012, "Invalid or unavailable delivery state")
        except (OSError, sqlite3.Error, KeyError, ValueError, TypeError):
            response = self.error(request.id, -32603, "Internal bridge error")
        return None if notification else response

    def serve(self, source=None, sink=None):
        source, sink = source or sys.stdin, sink or sys.stdout
        while line := source.readline(MAX_FRAME_CHARS + 1):
            if len(line) > MAX_FRAME_CHARS:
                while not line.endswith("\n") and line:
                    line = source.readline(MAX_FRAME_CHARS + 1)
                response = self.error(None, -32600, "Frame too large")
            else:
                try:
                    raw = json.loads(line)
                except (ValueError, RecursionError):
                    response = self.error(None, -32700, "Parse error")
                else:
                    response = self.handle(raw)
            if response is not None:
                try:
                    sink.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sink.flush()
                except BrokenPipeError:
                    return
