"""Read-only MCP tool implementations for the agent gateway."""

from __future__ import annotations

from collections.abc import Mapping

from .host import AgentGatewayHost, ToolArguments
from .protocol import bounded_lens_arguments as _bounded_lens_arguments


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

    def query(self, arguments: ToolArguments) -> dict[str, object]:
        return self._answer(arguments)

    def search(self, arguments: ToolArguments) -> dict[str, object]:
        return self.api.get_lens(_bounded_lens_arguments(arguments))

    def history(self, arguments: ToolArguments) -> dict[str, object]:
        raw_limit = arguments.get("limit")
        limit = 100 if raw_limit is None else int(raw_limit)
        return {
            "records": self.api.get_history(
                workspace_id=str(arguments["workspace_id"]),
                session_id=str(arguments.get("session_id") or "") or None,
                limit=min(1000, max(1, limit)),
            )
        }

    def memory_recall(self, arguments: ToolArguments) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        stream_ids = self._authorized_memory_stream_ids(arguments, workspace_id)
        return self.api.recall_memory(
            workspace_id=workspace_id,
            query_text=str(arguments.get("query_text") or ""),
            include_inferred=bool(arguments.get("include_inferred", True)),
            limit=arguments.get("limit"),
            authorized_stream_ids=stream_ids,
        )

    def memory_capture(self, arguments: ToolArguments) -> dict[str, object]:
        payload = arguments.get("record")
        if payload is None:
            payload = arguments.get("records")
        if payload is None:
            raise ValueError("memory_capture requires record or records")
        if isinstance(payload, Mapping):
            return self.api.capture_memory(payload)
        if isinstance(payload, list) and all(isinstance(item, Mapping) for item in payload):
            return self.api.capture_memory(payload)
        raise ValueError("memory_capture record(s) must be an object or list of objects")

    def memory_review(self, arguments: ToolArguments) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        return self.api.review_memory(
            workspace_id=workspace_id,
            kind=str(arguments.get("kind") or "").strip() or None,
            confidence=str(arguments.get("confidence") or "").strip() or None,
            lifecycle_status=str(arguments.get("lifecycle_status") or "").strip() or None,
            limit=int(arguments.get("limit") or 50),
            authorized_stream_ids=self._authorized_memory_stream_ids(arguments, workspace_id),
        )

    def multimodal_search(self, arguments: ToolArguments) -> dict[str, object]:
        return self.api.multimodal_search(arguments)

    def multimodal_status(self, arguments: ToolArguments) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        return self.api.multimodal_status(workspace_id=workspace_id)

    def hypergraph_search(self, arguments: ToolArguments) -> dict[str, object]:
        payload = _bounded_lens_arguments(arguments)
        payload.setdefault("max_hyperedges", 12)
        payload.setdefault("max_nodes", 40)
        payload.setdefault("max_edges", 80)
        return self.api.get_lens(payload)
