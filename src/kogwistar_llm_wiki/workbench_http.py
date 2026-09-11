"""Small optional HTTP transport for the app-owned workbench contract."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from urllib.parse import parse_qs, urlparse

import os

from .agent_gateway import AgentGateway, _jsonrpc_error, _jsonrpc_result
from .workbench_api import WorkbenchApi
from .identity import IdentityError, auth_mode, authorize, authenticate_bearer, claims_context

API_VERSION = "v1"
CAPABILITIES_SCHEMA_VERSION = "1"
MCP_READ_TOOLS = frozenset({
    "query", "search", "source", "status", "hypergraph_search", "history",
})


def build_workbench_handler(
    api: WorkbenchApi,
    gateway: AgentGateway | None = None,
) -> type[BaseHTTPRequestHandler]:
    gateway = gateway or AgentGateway(api)
    agent_api_enabled = os.getenv("LLM_WIKI_AGENT_API_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    api_token = os.getenv("LLM_WIKI_API_TOKEN", "").strip()
    # Resolve once during server construction so invalid auth configuration
    # fails before the REST listener accepts requests.
    selected_auth_mode = auth_mode()
    auth_required = selected_auth_mode != "disabled" and (bool(api_token) or os.getenv("LLM_WIKI_AUTH_REQUIRED", "").lower() in {"1", "true", "yes", "on"})
    configured_scopes = frozenset(filter(None, (item.strip() for item in os.getenv("LLM_WIKI_API_TOKEN_SCOPES", "read,write").split(","))))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path in {"/.well-known/agent.json", "/.well-known/agent-card.json", "/a2a/.well-known/agent-card"}:
                    self._require_agent_api()
                    base_url = os.getenv("LLM_WIKI_PUBLIC_BASE_URL", "").rstrip("/") or f"http://{self.headers.get('host', '127.0.0.1')}"
                    body = _agent_card(gateway.mcp_tool_names(), base_url=base_url, requires_auth=selected_auth_mode != "disabled")
                elif parsed.path == "/healthz":
                    body = {"ok": True, "service": "kogwistar-llm-wiki", "workspace_id": _first(query, "workspace_id", "default")}
                elif parsed.path == "/readyz":
                    body = api.readiness()
                    if not body.get("ready"):
                        self._write_json(body, status=503)
                        return
                elif parsed.path in {"/api/capabilities", "/v1/models"}:
                    body = _capabilities(gateway.mcp_tool_names()) if parsed.path == "/api/capabilities" else _models()
                elif parsed.path == "/api/settings":
                    workspace_id = _first(query, "workspace_id", "default")
                    self._require_scope("read", workspace_id)
                    body = api.get_settings(workspace_id=workspace_id)
                elif parsed.path == "/api/settings/health":
                    workspace_id = _first(query, "workspace_id", "default")
                    self._require_scope("read", workspace_id)
                    body = api.settings_health(workspace_id=workspace_id)
                elif parsed.path == "/api/compose/preview":
                    workspace_id = _first(query, "workspace_id", "default")
                    self._require_scope("read", workspace_id)
                    body = api.compose_preview({
                        "workspace_id": workspace_id,
                        "backend": _first(query, "backend", "postgres"),
                        "project_name": _first(query, "project_name", "llm-wiki"),
                        "mode": _first(query, "mode", "gpu"),
                        "auth_mode": _first(query, "auth_mode", "disabled"),
                        "model_revision": _first(query, "model_revision", ""),
                        "representation_dimension": int(_first(query, "representation_dimension", "1024")),
                        "with_otel": _first(query, "with_otel", "false").lower() in {"1", "true", "yes"},
                        "with_oauth": _first(query, "with_oauth", "false").lower() in {"1", "true", "yes"},
                    })
                elif parsed.path == "/api/models":
                    workspace_id = _first(query, "workspace_id", "default")
                    self._require_scope("read", workspace_id)
                    body = api.available_models(
                        _first(query, "role", "parser"),
                        provider=_first(query, "provider", "") or None,
                        base_url=_first(query, "base_url", "") or None,
                    )
                elif parsed.path.startswith("/a2a/v1/tasks/"):
                    self._require_agent_api()
                    workspace_id = _first(query, "workspace_id", "default")
                    self._require_scope("read", workspace_id)
                    task_id = parsed.path.rsplit("/", 1)[-1]
                    body = gateway.a2a_task(workspace_id=workspace_id, task_id=task_id)
                    if body is None:
                        self._write_json({"error": "not_found"}, status=404)
                        return
                elif parsed.path == "/mcp/tools/list":
                    self._require_agent_api()
                    self._require_scope("read")
                    descriptions = gateway.mcp_tool_descriptions()
                    body = {"tools": [{"name": name, "description": descriptions[name]} for name in gateway.mcp_tool_names()]}
                elif parsed.path == "/api/lens":
                    payload = {
                        "workspace_id": _first(query, "workspace_id", "rl-fixture"),
                        "query_text": _first(query, "query", ""),
                        "graph_spaces": tuple(query.get("graph_space", ["curated_kg"])),
                        "explicit_anchor_ids": tuple(query.get("explicit_anchor_id", [])),
                        "pinned_node_ids": tuple(query.get("pinned_node_id", [])),
                        "hop_limit": int(_first(query, "hop_limit", "1")),
                        "max_nodes": int(_first(query, "max_nodes", "40")),
                        "max_edges": int(_first(query, "max_edges", "80")),
                    }
                    self._require_scope("read", str(payload["workspace_id"]))
                    body = api.get_lens(payload)
                elif parsed.path == "/api/history":
                    workspace_id = _first(query, "workspace_id", "rl-fixture")
                    self._require_scope("read", workspace_id)
                    body = api.get_history(
                        workspace_id=workspace_id,
                        session_id=_first(query, "session_id", "") or None,
                        limit=int(_first(query, "limit", "100")),
                    )
                elif parsed.path == "/api/interactions":
                    workspace_id = _first(query, "workspace_id", "")
                    interaction_id = _first(query, "interaction_id", "")
                    if not workspace_id or not interaction_id:
                        raise ValueError("workspace_id and interaction_id are required")
                    self._require_scope("read", workspace_id)
                    body = api.get_interaction(
                        workspace_id=workspace_id,
                        interaction_id=interaction_id,
                    )
                    if body is None:
                        self._write_json({"error": "not_found"}, status=404)
                        return
                else:
                    self._write_json({"error": "not_found"}, status=404)
                    return
            except _RouteHandled:
                return
            except (KeyError, TypeError, ValueError) as exc:
                self._write_json({"error": "invalid_request", "detail": str(exc)}, status=400)
                return
            self._write_json(body)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            agent_paths = {"/a2a", "/v1/responses", "/v1/chat/completions", "/a2a/v1/message:send", "/a2a/v1/message:stream", "/mcp/tools/call"}
            if parsed.path not in {"/api/proposal/validate", "/api/proposal/confirm", "/api/ask", "/api/interactions", "/api/settings/desired", "/api/settings/apply", "/api/compose/preview", "/api/compose/check", *agent_paths}:
                self._write_json({"error": "not_found"}, status=404)
                return
            try:
                size = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                workspace_id = _payload_workspace(payload)
                if selected_auth_mode == "kogwistar_jwt" and workspace_id is None:
                    raise IdentityError("workspace_id is required when JWT authorization is enabled", status=400)
                if parsed.path in agent_paths:
                    self._require_agent_api()
                    if parsed.path == "/a2a":
                        method = payload.get("method") if isinstance(payload, dict) else None
                        self._require_scope("read" if method == "tasks/get" else "write", workspace_id)
                    elif parsed.path != "/mcp/tools/call":
                        self._require_scope("write" if parsed.path in {"/a2a/v1/message:send", "/a2a/v1/message:stream"} else "read", workspace_id)
                elif parsed.path in {"/api/ask", "/api/proposal/validate"}:
                    self._require_scope("read", workspace_id)
                else:
                    self._require_scope("write", workspace_id)
                if parsed.path == "/a2a":
                    rpc = gateway.a2a_jsonrpc(payload)
                    if payload.get("method") == "message/stream" and "result" in rpc:
                        self._write_a2a_stream(
                            rpc["result"],
                            payload.get("params") if isinstance(payload.get("params"), dict) else {},
                            gateway,
                            status=200,
                            jsonrpc_id=payload.get("id"),
                            standard=True,
                        )
                        return
                    body = rpc
                    status = 200 if "result" in rpc else 400
                elif parsed.path == "/v1/responses":
                    body = gateway.responses(payload)
                    status = 200
                elif parsed.path == "/v1/chat/completions":
                    body = gateway.chat_completions(payload)
                    status = 200
                elif parsed.path == "/a2a/v1/message:stream":
                    body = gateway.a2a_message(payload)
                    status = 202 if body.get("status", {}).get("state") == "working" else 200
                    self._write_a2a_stream(body, payload, gateway, status=status)
                    return
                elif parsed.path == "/a2a/v1/message:send":
                    body = gateway.a2a_message(payload)
                    status = 202 if body.get("status", {}).get("state") == "working" else 200
                elif parsed.path == "/mcp/tools/call":
                    name = payload.get("name")
                    arguments = payload.get("arguments") or {}
                    if not isinstance(name, str) or not isinstance(arguments, dict):
                        raise ValueError("MCP tool call requires string name and object arguments")
                    workspace_id = str(arguments.get("workspace_id") or "") or None
                    if name in {"propose"} and not workspace_id:
                        request = arguments.get("request")
                        workspace_id = str(request.get("workspace_id") or "") if isinstance(request, dict) else None
                    self._require_scope("read" if name in MCP_READ_TOOLS else "write", workspace_id)
                    structured = gateway.call_mcp_tool(name, arguments)
                    body = {"content": [{"type": "text", "text": json.dumps(structured, sort_keys=True, default=str)}], "structuredContent": structured}
                    status = 200
                elif parsed.path == "/api/ask":
                    body = api.ask(payload)
                    status = 200
                elif parsed.path == "/api/interactions":
                    body = api.submit_interaction(payload)
                    status = 202
                elif parsed.path == "/api/proposal/confirm":
                    body = api.confirm_cockpit_proposal(payload)
                    status = 200
                elif parsed.path == "/api/settings/desired":
                    changes = payload.get("settings")
                    if not isinstance(changes, dict):
                        raise ValueError("settings must be an object")
                    body = api.update_desired_settings(
                        workspace_id=str(workspace_id or "default"), changes=changes
                    )
                    status = 200
                elif parsed.path == "/api/settings/apply":
                    body = api.apply_settings(
                        workspace_id=str(workspace_id or "default"),
                        confirmed=bool(payload.get("confirmed", False)),
                    )
                    status = 200
                elif parsed.path == "/api/compose/preview":
                    body = api.compose_preview(payload)
                    status = 200
                elif parsed.path == "/api/compose/check":
                    body = api.compose_check(payload)
                    status = 200
                else:
                    body = api.validate_proposal(payload)
                    status = 200
            except _RouteHandled:
                return
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._write_json({"error": "invalid_request", "detail": str(exc)}, status=400)
                return
            except RuntimeError as exc:
                self._write_json({"error": "service_unavailable", "detail": str(exc)}, status=503)
                return
            self._write_json(body, status=status)

        def _require_agent_api(self) -> None:
            if not agent_api_enabled:
                self._write_json({"error": "agent_api_disabled", "detail": "Set LLM_WIKI_AGENT_API_ENABLED=true to enable agent protocol routes"}, status=404)
                raise _RouteHandled

        def _require_scope(self, scope: str, workspace_id: str | None = None) -> None:
            try:
                identity = authenticate_bearer(self.headers.get("authorization"))
                authorize(identity, workspace_id=workspace_id, scope=scope)
                self._identity_context = identity
                self._identity_context_manager = claims_context(identity)
                self._identity_context_manager.__enter__()
            except IdentityError as exc:
                error = "unauthorized" if exc.status == 401 else "forbidden" if exc.status == 403 else "auth_configuration_error"
                self._write_json({"error": error, "detail": str(exc)}, status=exc.status)
                raise _RouteHandled

        def finish(self) -> None:
            context = getattr(self, "_identity_context_manager", None)
            if context is not None:
                context.__exit__(None, None, None)
                self._identity_context_manager = None
            super().finish()

        def _write_json(self, body: object, *, status: int = 200) -> None:
            encoded = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _write_sse(self, body: object, *, status: int = 200) -> None:
            encoded = _sse_bytes("task", body)
            self.send_response(status)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _write_a2a_stream(
            self,
            initial: dict[str, object],
            payload: dict[str, object],
            gateway: AgentGateway,
            *,
            status: int,
            jsonrpc_id: object | None = None,
            standard: bool = False,
        ) -> None:
            """Stream durable task state until completion or bounded timeout."""
            self.send_response(status)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()

            def emit(body: object) -> None:
                value = _jsonrpc_result(jsonrpc_id, body) if standard else body
                self.wfile.write(_sse_bytes("message" if standard else "task", value))
                self.wfile.flush()

            emit(initial)
            if initial.get("status", {}).get("state") not in {"submitted", "working"}:
                return
            metadata = payload.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            workspace_id = str(payload.get("workspace_id") or metadata.get("workspace_id") or "default")
            task_id = str(initial.get("id") or "")
            try:
                timeout = max(1.0, float(os.getenv("LLM_WIKI_A2A_STREAM_TIMEOUT_SECONDS", "300")))
                interval = max(0.1, float(os.getenv("LLM_WIKI_A2A_STREAM_POLL_SECONDS", "0.5")))
            except ValueError:
                timeout, interval = 300.0, 0.5
            deadline = time.monotonic() + timeout
            previous = json.dumps(initial, sort_keys=True, default=str)
            while time.monotonic() < deadline:
                time.sleep(interval)
                if standard:
                    response = gateway.a2a_jsonrpc({
                        "jsonrpc": "2.0",
                        "id": jsonrpc_id,
                        "method": "tasks/get",
                        "params": {"id": task_id, "metadata": {"workspace_id": workspace_id}},
                    })
                    current = response.get("result") if isinstance(response.get("result"), dict) else None
                else:
                    current = gateway.a2a_task(workspace_id=workspace_id, task_id=task_id)
                if current is None:
                    emit({"id": task_id, "status": {"state": "failed", "message": "task_not_found"}})
                    return
                encoded = json.dumps(current, sort_keys=True, default=str)
                if encoded != previous:
                    emit(current)
                    previous = encoded
                if current.get("status", {}).get("state") not in {"submitted", "working"}:
                    return
            emit({
                "id": task_id,
                "contextId": workspace_id,
                "status": {"state": "working"},
                "metadata": {"workspace_id": workspace_id, "stream_timeout": True},
            })

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


class _RouteHandled(Exception):
    """Internal control flow after writing a guarded route response."""


def serve_workbench(api: WorkbenchApi, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve lens/history reads until interrupted; mutations remain command-gated."""
    server = ThreadingHTTPServer((host, port), build_workbench_handler(api))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        api.close()


def _first(values: dict[str, list[str]], key: str, default: str) -> str:
    return str((values.get(key) or [default])[0])


def _payload_workspace(payload: dict[str, object]) -> str | None:
    """Resolve a protocol envelope's workspace without silently inventing one."""
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    params = payload.get("params")
    params = params if isinstance(params, dict) else {}
    nested_metadata = params.get("metadata")
    nested_metadata = nested_metadata if isinstance(nested_metadata, dict) else {}
    arguments = payload.get("arguments")
    arguments = arguments if isinstance(arguments, dict) else {}
    request = arguments.get("request")
    request = request if isinstance(request, dict) else {}
    values = [
        value
        for value in (
            payload.get("workspace_id"),
            metadata.get("workspace_id"),
            params.get("workspace_id"),
            nested_metadata.get("workspace_id"),
            arguments.get("workspace_id"),
            request.get("workspace_id"),
        )
        if value is not None and str(value).strip()
    ]
    normalized = {str(value).strip() for value in values}
    if len(normalized) > 1:
        raise ValueError("conflicting workspace_id values in request envelope")
    return next(iter(normalized), None)


def _sse_bytes(event: str, body: object) -> bytes:
    return (
        f"event: {event}\ndata: {json.dumps(body, sort_keys=True, default=str)}\n\n"
    ).encode("utf-8")


def _agent_card(
    mcp_tools: tuple[str, ...],
    *,
    base_url: str = "",
    requires_auth: bool = False,
) -> dict[str, object]:
    endpoint = f"{base_url}/a2a" if base_url else "/a2a"
    skills = [
        {
            "id": tool,
            "name": tool.replace("_", " ").title(),
            "description": f"LLM-Wiki {tool.replace('_', ' ')} capability.",
            "tags": ["knowledge-management", tool],
            "inputModes": ["text", "application/json"],
            "outputModes": ["text", "application/json"],
        }
        for tool in mcp_tools
    ]
    card: dict[str, object] = {
        "protocolVersion": "0.2.6",
        "name": "llm-wiki",
        "description": "Grounded knowledge management with explicit proposal workflow",
        "url": endpoint,
        "preferredTransport": "JSONRPC",
        "additionalInterfaces": [{"url": f"{base_url}/a2a/v1/message:send" if base_url else "/a2a/v1/message:send", "transport": "HTTP+JSON"}],
        "version": API_VERSION,
        "capabilities": {"streaming": True, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["text", "application/json"],
        "defaultOutputModes": ["text", "application/json"],
        "skills": skills,
    }
    if requires_auth:
        card["securitySchemes"] = {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        card["security"] = [{"bearerAuth": []}]
    return {
        **card,
        "llm_wiki": {"mcp_tools": list(mcp_tools)},
    }


def _capabilities(mcp_tools: tuple[str, ...]) -> dict[str, object]:
    return {
        "service": "kogwistar-llm-wiki",
        "api_version": API_VERSION,
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "protocols": {"rest": True, "openai_responses": True, "openai_chat": True, "a2a": True, "mcp": True},
        "mcp_tools": list(mcp_tools),
        "modes": ["deterministic", "codex"],
        "mutation_policy": "validate_then_explicit_confirm",
    }


def _models() -> dict[str, object]:
    return {"object": "list", "data": [{"id": "llm-wiki-deterministic", "object": "model", "owned_by": "kogwistar-llm-wiki"}]}


__all__ = ["build_workbench_handler", "serve_workbench"]
