"""Optional FastMCP adapter for the llm-wiki AgentGateway."""

from __future__ import annotations

from typing import Any

from .agent_gateway import AgentGateway


def build_agent_mcp(gateway: AgentGateway) -> Any:
    try:
        from fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install the optional 'agent' extra to serve MCP: pip install -e '.[agent]'") from exc

    mcp = FastMCP("llm-wiki")

    @mcp.tool(name="llm_wiki.ask")
    def ask(workspace_id: str, query_text: str, session_id: str = "default", mode: str = "deterministic") -> dict[str, object]:
        return gateway.call_mcp_tool("llm_wiki.ask", {"workspace_id": workspace_id, "query_text": query_text, "session_id": session_id, "mode": mode})

    @mcp.tool(name="llm_wiki.search")
    def search(workspace_id: str, query_text: str, hop_limit: int = 1, max_nodes: int = 40) -> dict[str, object]:
        return gateway.call_mcp_tool("llm_wiki.search", {"workspace_id": workspace_id, "query_text": query_text, "hop_limit": hop_limit, "max_nodes": max_nodes})

    @mcp.tool(name="llm_wiki.history")
    def history(workspace_id: str, session_id: str | None = None, limit: int = 100) -> dict[str, object]:
        return gateway.call_mcp_tool("llm_wiki.history", {"workspace_id": workspace_id, "session_id": session_id, "limit": limit})

    @mcp.tool(name="llm_wiki.propose")
    def propose(request: dict[str, Any], proposal: dict[str, Any]) -> dict[str, object]:
        return gateway.call_mcp_tool("llm_wiki.propose", {"request": request, "proposal": proposal})

    @mcp.tool(name="llm_wiki.confirm")
    def confirm(workspace_id: str, interaction_id: str, confirmed: bool, proposal: dict[str, Any] | None = None) -> dict[str, object]:
        return gateway.call_mcp_tool("llm_wiki.confirm", {"workspace_id": workspace_id, "interaction_id": interaction_id, "confirmed": confirmed, "proposal": proposal})

    return mcp


__all__ = ["build_agent_mcp"]
