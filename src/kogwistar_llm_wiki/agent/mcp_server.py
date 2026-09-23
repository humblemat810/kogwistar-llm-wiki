"""Official MCP SDK adapter for the LLM-Wiki agent gateway.

The adapter owns protocol and transport concerns only. Domain behavior remains
in :class:`AgentGateway`, which makes this implementation usable by both
CPython and PyPy without a framework-specific code path.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

from ..codex.codex_memory import CodexMemoryRecord
from ..configuration.identity import (
    LlmWikiIdentity,
    auth_mode,
    authenticate_bearer,
    authorize,
    claims_context,
)
from .gateway import AgentGateway

_MCP_REQUEST_HEADERS: ContextVar[dict[str, str] | None] = ContextVar(
    "llm_wiki_mcp_request_headers", default=None
)

READ_TOOL_NAMES = frozenset(
    {
        "query",
        "search",
        "source",
        "status",
        "hypergraph_search",
        "history",
        "memory_recall",
        "memory_review",
    }
)


@dataclass(frozen=True, slots=True)
class McpAuthSettings:
    """Effective MCP authentication settings exposed for diagnostics."""

    mode: str
    required: bool
    scopes: frozenset[str]


def _env_value(name: str, fallback_name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    return value or os.getenv(fallback_name, default).strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _object_schema(
    properties: dict[str, dict[str, object]],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
        "type": "object",
    }


def _memory_record_schema() -> dict[str, object]:
    """Expose the same nested contract used by memory persistence.

    The MCP SDK publishes this schema to clients, but the gateway still
    validates the payload with ``CodexMemoryRecord`` before persistence.  Keep
    Pydantic's definitions at the tool-schema root so nested ``$ref`` values
    resolve correctly for MCP clients.
    """

    schema = CodexMemoryRecord.model_json_schema()
    definitions = schema.pop("$defs", None)
    if not isinstance(definitions, dict):
        definitions = {}
    schema.pop("title", None)
    schema["$defs"] = definitions
    return schema


def _tool_specs() -> tuple[tuple[str, str, dict[str, object]], ...]:
    """Return the frozen input contracts emitted by the previous adapter."""

    def string(**extra: object) -> dict[str, object]:
        return {"type": "string", **extra}
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}
    object_value = {"additionalProperties": True, "type": "object"}
    nullable_object = {"anyOf": [object_value, {"type": "null"}], "default": None}
    nullable_string_list = {
        "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
        "default": None,
    }
    nullable_integer = {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None}
    nullable_number = {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None}
    memory_record = _memory_record_schema()
    memory_definitions = memory_record.pop("$defs", {})
    memory_record_nullable = {"anyOf": [memory_record, {"type": "null"}], "default": None}
    memory_records = {
        "anyOf": [
            {"items": memory_record, "maxItems": 32, "minItems": 1, "type": "array"},
            {"type": "null"},
        ],
        "default": None,
    }

    return (
        (
            "query",
            "Ask a grounded question about existing wiki knowledge.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "query_text": string(),
                    "session_id": string(default="default"),
                    "mode": string(default="deterministic"),
                },
                required=("workspace_id", "query_text"),
            ),
        ),
        (
            "search",
            "Retrieve bounded relevant knowledge and supporting context.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "query_text": string(),
                    "hop_limit": {"default": 1, "type": "integer"},
                    "max_nodes": {"default": 40, "type": "integer"},
                },
                required=("workspace_id", "query_text"),
            ),
        ),
        (
            "ingest",
            "Capture a raw source through the canonical ingestion pipeline.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "source_uri": string(),
                    "raw_text": nullable_string,
                    "title": string(default=""),
                    "provenance_policy": string(default="optional"),
                    "provenance": nullable_object,
                },
                required=("workspace_id", "source_uri"),
            ),
        ),
        (
            "source",
            "Inspect a source by workspace_id plus source_uri or source_document_id.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "source_uri": string(default=""),
                    "source_document_id": string(default=""),
                },
                required=("workspace_id",),
            ),
        ),
        (
            "reingest",
            "Update an existing source using source_uri or source_document_id.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "source_uri": string(default=""),
                    "source_document_id": string(default=""),
                    "raw_text": nullable_string,
                    "title": string(default=""),
                    "provenance_policy": nullable_string,
                    "provenance": nullable_object,
                },
                required=("workspace_id",),
            ),
        ),
        (
            "maintain",
            "Queue bounded maintenance with optional structured prior-round context.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "topic": string(default=""),
                    "objective": string(default=""),
                    "source_document_ids": nullable_string_list,
                    "seed_node_ids": nullable_string_list,
                    "maintenance_context": nullable_object,
                    "max_rounds": nullable_integer,
                    "max_time_seconds": nullable_number,
                    "max_llm_calls": nullable_integer,
                    "max_tokens": nullable_integer,
                    "max_cost_usd": nullable_number,
                    "max_steps": nullable_integer,
                },
                required=("workspace_id",),
            ),
        ),
        (
            "status",
            "Report wiki health, source state, graph quality, and maintenance state.",
            _object_schema({"workspace_id": string()}, required=("workspace_id",)),
        ),
        (
            "hypergraph_search",
            "Inspect bounded raw graph and hypergraph structure read-only.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "query_text": string(default=""),
                    "hop_limit": {"default": 1, "type": "integer"},
                    "max_nodes": {"default": 40, "type": "integer"},
                    "max_edges": {"default": 80, "type": "integer"},
                    "max_hyperedges": {"default": 12, "type": "integer"},
                    "include_tombstones": {"default": False, "type": "boolean"},
                },
                required=("workspace_id",),
            ),
        ),
        (
            "history",
            "Inspect prior investigations, decisions, and grounding metadata.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "session_id": nullable_string,
                    "limit": {"default": 100, "type": "integer"},
                },
                required=("workspace_id",),
            ),
        ),
        (
            "memory_recall",
            "Recall bounded, evidence-backed project memory for relevant work.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "query_text": string(default=""),
                    "include_inferred": {"default": True, "type": "boolean"},
                    "limit": {"default": 12, "type": "integer"},
                },
                required=("workspace_id",),
            ),
        ),
        (
            "memory_capture",
            "Capture structured, evidence-backed project memory when enabled.",
            _object_schema(
                {
                    "record": memory_record_nullable,
                    "records": memory_records,
                }
            )
            | {"$defs": memory_definitions},
        ),
        (
            "memory_review",
            "Review project memory records, evidence, lifecycle, and conflicts.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "kind": string(default=""),
                    "confidence": string(default=""),
                    "lifecycle_status": string(default=""),
                    "limit": {"default": 50, "type": "integer"},
                },
                required=("workspace_id",),
            ),
        ),
        (
            "propose",
            "Validate a candidate durable knowledge change without applying it.",
            _object_schema(
                {"request": object_value, "proposal": object_value},
                required=("request", "proposal"),
            ),
        ),
        (
            "confirm",
            "Explicitly approve and apply a previously validated change.",
            _object_schema(
                {
                    "workspace_id": string(),
                    "interaction_id": string(),
                    "confirmed": {"type": "boolean"},
                    "proposal": nullable_object,
                },
                required=("workspace_id", "interaction_id", "confirmed"),
            ),
        ),
    )


class AgentMcpServer:
    """Protocol adapter backed by the official MCP low-level server."""

    def __init__(self, gateway: AgentGateway) -> None:
        self.gateway = gateway
        self.server = Server(
            "llm-wiki",
            on_list_tools=self._handle_list_tools,
            on_call_tool=self._handle_call_tool,
        )
        self._tools = tuple(
            types.Tool(name=name, description=description, input_schema=schema)
            for name, description, schema in _tool_specs()
        )
        selected_mode = auth_mode()
        required = _truthy(
            _env_value("LLM_WIKI_MCP_AUTH_REQUIRED", "LLM_WIKI_AUTH_REQUIRED")
        )
        token = _env_value("LLM_WIKI_MCP_TOKEN", "LLM_WIKI_API_TOKEN")
        if selected_mode == "static_token" and required and not token:
            raise RuntimeError(
                "MCP authentication is required but no token is configured; "
                "set LLM_WIKI_MCP_TOKEN or LLM_WIKI_API_TOKEN"
            )
        if selected_mode == "kogwistar_jwt" and not os.getenv("JWT_SECRET", "").strip():
            raise RuntimeError(
                "MCP JWT authentication is enabled but JWT_SECRET is missing; "
                "configure the Kogwistar JWT settings before starting the server"
            )
        configured_scopes = frozenset(
            item.strip()
            for item in _env_value(
                "LLM_WIKI_MCP_TOKEN_SCOPES", "LLM_WIKI_API_TOKEN_SCOPES", "read,write"
            ).split(",")
            if item.strip()
        )
        self.auth = (
            McpAuthSettings(selected_mode, required, configured_scopes)
            if selected_mode != "disabled" and required
            else None
        )

    async def list_tools(self) -> list[types.Tool]:
        """Return the complete tool contract for local inspection and tests."""

        return list(self._tools)

    async def _list_tools(self) -> list[types.Tool]:
        """Compatibility helper for callers that inspected the old registry."""

        return await self.list_tools()

    async def _handle_list_tools(self, _context: Any, _params: Any) -> types.ListToolsResult:
        self._authenticate_request()
        return types.ListToolsResult(tools=list(self._tools))

    async def _handle_call_tool(
        self, _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        try:
            identity = self._authenticate_request()
            result = self._dispatch(
                params.name,
                params.arguments or {},
                identity=identity,
            )
        except Exception as exc:  # noqa: BLE001 - expose failures as tool results
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))],
                is_error=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, indent=2))],
            structured_content=result,
        )

    def _authenticate_request(self) -> LlmWikiIdentity | None:
        if self.auth is None:
            return None
        headers = _MCP_REQUEST_HEADERS.get() or {}
        authorization = headers.get("authorization")
        return authenticate_bearer(authorization)

    @staticmethod
    def _headers_from_scope(scope: Any) -> dict[str, str]:
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }

    async def _with_request_headers(
        self, scope: Any, operation: Any
    ) -> None:
        token = _MCP_REQUEST_HEADERS.set(self._headers_from_scope(scope))
        try:
            await operation()
        finally:
            _MCP_REQUEST_HEADERS.reset(token)

    def _dispatch(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        identity: LlmWikiIdentity | None = None,
    ) -> dict[str, object]:
        """Authorize and dispatch one validated MCP call to the gateway."""

        workspace = str(arguments.get("workspace_id") or "").strip() or None
        if name == "propose" and not workspace:
            request = arguments.get("request")
            workspace = (
                str(request.get("workspace_id") or "").strip()
                if isinstance(request, dict)
                else None
            ) or None
        if name == "memory_capture" and not workspace:
            candidates = arguments.get("records") or arguments.get("record")
            if isinstance(candidates, dict):
                workspace = str(candidates.get("workspace_id") or "").strip() or None
            elif isinstance(candidates, list):
                workspaces = {
                    str(item.get("workspace_id") or "").strip()
                    for item in candidates
                    if isinstance(item, dict) and str(item.get("workspace_id") or "").strip()
                }
                if len(workspaces) == 1:
                    workspace = next(iter(workspaces))
        scope = "read" if name in READ_TOOL_NAMES else "write"
        authorize(identity, workspace_id=workspace, scope=scope)
        with claims_context(identity):
            return self.gateway.call_mcp_tool(name, arguments)

    async def call_tool(
        self, name: str, arguments: dict[str, object] | None = None
    ) -> types.CallToolResult:
        """Invoke a tool directly for provider-free contract tests."""

        try:
            result = self._dispatch(name, arguments or {})
        except Exception as exc:  # noqa: BLE001 - MCP tools expose errors as protocol results
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))],
                is_error=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, indent=2))],
            structured_content=result,
        )

    async def _run_stdio(self) -> None:
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )

    def _streamable_http_app(self, path: str) -> Any:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Mount

        endpoint = path.rstrip("/") or "/"
        manager_holder: dict[str, StreamableHTTPSessionManager] = {}

        @asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            manager = StreamableHTTPSessionManager(self.server)
            manager_holder["manager"] = manager
            async with manager.run():
                try:
                    yield
                finally:
                    manager_holder.pop("manager", None)

        async def scoped_handler(scope: Any, receive: Any, send: Any) -> None:
            if scope.get("type") == "http":
                request_path = str(scope.get("path") or "")
                if request_path not in {endpoint, endpoint + "/"}:
                    await PlainTextResponse("Not Found", status_code=404)(
                        scope, receive, send
                    )
                    return
            manager = manager_holder.get("manager")
            if manager is None:
                await PlainTextResponse("MCP server is not started", status_code=503)(
                    scope, receive, send
                )
                return
            await self._with_request_headers(
                scope, lambda: manager.handle_request(scope, receive, send)
            )

        # Mount at the root of this small ASGI app and perform exact endpoint
        # matching ourselves.  A direct Starlette Mount(endpoint) redirects
        # `/mcp` to `/mcp/`, which changes the established client contract.
        return Starlette(
            routes=[Mount("/", app=scoped_handler)],
            lifespan=lifespan,
        )

    def _sse_app(self, path: str) -> Any:
        from mcp.server.sse import SseServerTransport
        from starlette.responses import PlainTextResponse

        endpoint = path.rstrip("/") or "/mcp"
        messages_path = f"{endpoint}/messages/"
        transport = SseServerTransport(messages_path)

        async def app(scope: dict[str, object], receive: Any, send: Any) -> None:
            request_path = str(scope.get("path") or "")
            if request_path == endpoint:
                async with transport.connect_sse(scope, receive, send) as streams:
                    read_stream, write_stream = streams
                    await self._with_request_headers(
                        scope,
                        lambda: self.server.run(
                            read_stream,
                            write_stream,
                            self.server.create_initialization_options(),
                        ),
                    )
                return
            if request_path == messages_path.rstrip("/"):
                await self._with_request_headers(
                    scope,
                    lambda: transport.handle_post_message(scope, receive, send),
                )
                return
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)

        return app

    def run(
        self,
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8780,
        path: str = "/mcp",
    ) -> None:
        """Run the official SDK transport without reimplementing MCP framing."""

        if transport == "stdio":
            asyncio.run(self._run_stdio())
            return
        import uvicorn

        if transport == "streamable-http":
            app = self._streamable_http_app(path)
        elif transport == "http":
            app = self._sse_app(path)
        else:
            raise ValueError(f"unsupported MCP transport: {transport}")
        uvicorn.run(app, host=host, port=port)


def build_agent_mcp(gateway: AgentGateway) -> AgentMcpServer:
    return AgentMcpServer(gateway)


__all__ = ["AgentMcpServer", "McpAuthSettings", "build_agent_mcp"]
