"""Read-only MCP tool implementations for the agent gateway."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from kogwistar.json_types import JsonValue

from .host import AgentGatewayHost, JsonObject, ToolArguments
from .protocol import bounded_lens_arguments as _bounded_lens_arguments


def _integer_argument(value: JsonValue | None, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise TypeError("limit must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise TypeError("limit must be an integer") from exc
    raise TypeError("limit must be an integer")


class AgentReadToolsMixin(AgentGatewayHost):
    def _authorized_memory_stream_ids(self, arguments: ToolArguments, workspace_id: str) -> tuple[str, ...]:
        raw_stream_ids = arguments.get("stream_ids") or []
        if not isinstance(raw_stream_ids, list) or not all(
            isinstance(stream_id, str) and stream_id.strip() for stream_id in raw_stream_ids
        ):
            raise ValueError("memory stream_ids must be a list of non-empty strings")
        stream_ids = tuple(str(stream_id).strip() for stream_id in raw_stream_ids)
        if any(
            not self.api.authorize_resource(
                workspace_id, "memory_stream", stream_id, "read"
            )
            for stream_id in stream_ids
        ):
            raise PermissionError("memory stream is not authorized for workspace")
        return stream_ids

    def query(self, arguments: ToolArguments) -> JsonObject:
        return self._answer(arguments)

    def search(self, arguments: ToolArguments) -> JsonObject:
        return cast(JsonObject, self.api.get_lens(_bounded_lens_arguments(arguments)))

    def history(self, arguments: ToolArguments) -> JsonObject:
        raw_limit = arguments.get("limit")
        limit = _integer_argument(raw_limit, default=100)
        return cast(JsonObject, {
            "records": self.api.get_history(
                workspace_id=str(arguments["workspace_id"]),
                session_id=str(arguments.get("session_id") or "") or None,
                limit=min(1000, max(1, limit)),
            )
        })

    def memory_recall(self, arguments: ToolArguments) -> JsonObject:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        stream_ids = self._authorized_memory_stream_ids(arguments, workspace_id)
        return cast(JsonObject, self.api.recall_memory(
            workspace_id=workspace_id,
            query_text=str(arguments.get("query_text") or ""),
            include_inferred=bool(arguments.get("include_inferred", True)),
            limit=_integer_argument(arguments.get("limit"), default=100),
            authorized_stream_ids=stream_ids,
        ))

    def memory_capture(self, arguments: ToolArguments) -> JsonObject:
        payload = arguments.get("record")
        if payload is None:
            payload = arguments.get("records")
        if payload is None:
            raise ValueError("memory_capture requires record or records")
        if isinstance(payload, Mapping):
            return self.api.capture_memory(cast(Mapping[str, JsonValue], payload))
        if isinstance(payload, list) and all(isinstance(item, Mapping) for item in payload):
            records = [cast(Mapping[str, JsonValue], item) for item in payload]
            return self.api.capture_memory(records)
        raise ValueError("memory_capture record(s) must be an object or list of objects")

    def memory_review(self, arguments: ToolArguments) -> JsonObject:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        return cast(JsonObject, self.api.review_memory(
            workspace_id=workspace_id,
            kind=str(arguments.get("kind") or "").strip() or None,
            confidence=str(arguments.get("confidence") or "").strip() or None,
            lifecycle_status=str(arguments.get("lifecycle_status") or "").strip() or None,
            limit=_integer_argument(arguments.get("limit"), default=50),
            authorized_stream_ids=self._authorized_memory_stream_ids(arguments, workspace_id),
        ))

    def multimodal_search(self, arguments: ToolArguments) -> JsonObject:
        return cast(JsonObject, self.api.multimodal_search(arguments))

    def multimodal_status(self, arguments: ToolArguments) -> JsonObject:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        return cast(JsonObject, self.api.multimodal_status(workspace_id=workspace_id))

    def hypergraph_search(self, arguments: ToolArguments) -> JsonObject:
        payload = _bounded_lens_arguments(arguments)
        payload.setdefault("max_hyperedges", 12)
        payload.setdefault("max_nodes", 40)
        payload.setdefault("max_edges", 80)
        return cast(JsonObject, self.api.get_lens(payload))
