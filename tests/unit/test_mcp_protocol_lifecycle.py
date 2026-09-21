from __future__ import annotations

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.memory import create_connected_server_and_client_session

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
        async with create_connected_server_and_client_session(server.server) as client:
            initialized = await client.initialize()
            assert initialized.serverInfo.name == "llm-wiki"

            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"status", "maintain", "memory_recall"}.issubset(names)

            result = await client.call_tool("status", {"workspace_id": "demo"})
            assert result.isError is False
            assert result.structuredContent == {
                "tool": "status",
                "workspace_id": "demo",
                "ok": True,
            }

    anyio.run(exercise)
    assert gateway.calls == [("status", {"workspace_id": "demo"})]


def test_official_mcp_streamable_http_round_trip() -> None:
    gateway = _Gateway()
    server = AgentMcpServer(gateway)
    app = server._streamable_http_app("/mcp")

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                follow_redirects=True,
                transport=httpx.ASGITransport(app=app),
            ) as http_client:
                async with streamable_http_client(
                    "http://testserver/mcp",
                    http_client=http_client,
                ) as (read_stream, write_stream, _get_session_id):
                    async with ClientSession(read_stream, write_stream) as client:
                        await client.initialize()
                        result = await client.call_tool("status", {"workspace_id": "http-demo"})
                        assert result.isError is False
                        assert result.structuredContent == {
                            "tool": "status",
                            "workspace_id": "http-demo",
                            "ok": True,
                        }

    anyio.run(exercise)
    assert gateway.calls == [("status", {"workspace_id": "http-demo"})]
