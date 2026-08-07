"""Small optional HTTP transport for the app-owned workbench contract."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import parse_qs, urlparse

from .workbench_api import WorkbenchApi


def build_workbench_handler(api: WorkbenchApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/lens":
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
                    body = api.get_lens(payload)
                elif parsed.path == "/api/history":
                    body = api.get_history(
                        workspace_id=_first(query, "workspace_id", "rl-fixture"),
                        session_id=_first(query, "session_id", "") or None,
                        limit=int(_first(query, "limit", "100")),
                    )
                elif parsed.path == "/api/interactions":
                    workspace_id = _first(query, "workspace_id", "")
                    interaction_id = _first(query, "interaction_id", "")
                    if not workspace_id or not interaction_id:
                        raise ValueError("workspace_id and interaction_id are required")
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
            except (KeyError, TypeError, ValueError) as exc:
                self._write_json({"error": "invalid_request", "detail": str(exc)}, status=400)
                return
            self._write_json(body)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path not in {"/api/proposal/validate", "/api/proposal/confirm", "/api/ask", "/api/interactions"}:
                self._write_json({"error": "not_found"}, status=404)
                return
            try:
                size = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(size))
                if parsed.path == "/api/ask":
                    body = api.ask(payload)
                    status = 200
                elif parsed.path == "/api/interactions":
                    body = api.submit_interaction(payload)
                    status = 202
                elif parsed.path == "/api/proposal/confirm":
                    body = api.confirm_cockpit_proposal(payload)
                    status = 200
                else:
                    body = api.validate_proposal(payload)
                    status = 200
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._write_json({"error": "invalid_request", "detail": str(exc)}, status=400)
                return
            except RuntimeError as exc:
                self._write_json({"error": "service_unavailable", "detail": str(exc)}, status=503)
                return
            self._write_json(body, status=status)

        def _write_json(self, body: object, *, status: int = 200) -> None:
            encoded = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


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


__all__ = ["build_workbench_handler", "serve_workbench"]
