from __future__ import annotations

import anyio
import httpx
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from kogwistar_llm_wiki.agent.mcp_server import AgentMcpServer

pytestmark = pytest.mark.ci


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_mcp_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        return {
            "tool": name,
            "workspace_id": arguments.get("workspace_id"),
            "ok": True,
        }


def test_official_mcp_server_completes_real_session_lifecycle() -> None:
    gateway = _Gateway()
    server = AgentMcpServer(gateway)

    async def exercise() -> None:
        async with Client(server.server) as client:
            assert client.server_info is not None
            assert client.server_info.name == "llm-wiki"
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"status", "maintain", "memory_recall"}.issubset(names)

            result = await client.call_tool("status", {"workspace_id": "demo"})
            assert result.is_error is False
            assert result.structured_content == {
                "tool": "status",
                "workspace_id": "demo",
                "ok": True,
            }

    anyio.run(exercise)
    assert gateway.calls == [("status", {"workspace_id": "demo"})]


def test_official_mcp_streamable_http_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_MCP_AUTH_REQUIRED", "true")
    monkeypatch.setenv("LLM_WIKI_MCP_TOKEN", "test-token")
    gateway = _Gateway()
    server = AgentMcpServer(gateway)
    app = server._streamable_http_app("/mcp")

    async def exercise() -> None:
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            headers={"Authorization": "Bearer test-token"},
            follow_redirects=True,
            transport=httpx.ASGITransport(app=app),
        ) as http_client, Client(
            streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            )
        ) as client:
            result = await client.call_tool("status", {"workspace_id": "http-demo"})
            assert result.is_error is False
            assert result.structured_content == {
                "tool": "status",
                "workspace_id": "http-demo",
                "ok": True,
            }
            write_result = await client.call_tool(
                "maintain", {"workspace_id": "http-demo", "topic": "mcp"}
            )
            assert write_result.is_error is False
            assert write_result.structured_content == {
                "tool": "maintain",
                "workspace_id": "http-demo",
                "ok": True,
            }

    anyio.run(exercise)
    assert gateway.calls == [
        ("status", {"workspace_id": "http-demo"}),
        ("maintain", {"workspace_id": "http-demo", "topic": "mcp"}),
    ]
