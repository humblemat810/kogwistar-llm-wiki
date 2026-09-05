"""Optional FastMCP adapter for the llm-wiki AgentGateway."""

from __future__ import annotations

from typing import Any
import os

from .agent_gateway import AgentGateway


def _env_value(name: str, fallback_name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    return value or os.getenv(fallback_name, default).strip()


def build_agent_mcp(gateway: AgentGateway) -> Any:
    try:
        from fastmcp import FastMCP
        from fastmcp.server.auth import StaticTokenVerifier, require_scopes
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install the optional 'agent' extra to serve MCP: pip install -e '.[agent]'") from exc

    token = _env_value("LLM_WIKI_MCP_TOKEN", "LLM_WIKI_API_TOKEN")
    auth_required = _env_value("LLM_WIKI_MCP_AUTH_REQUIRED", "LLM_WIKI_AUTH_REQUIRED").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if auth_required and not token:
        raise RuntimeError(
            "MCP authentication is required but no token is configured; "
            "set LLM_WIKI_MCP_TOKEN or LLM_WIKI_API_TOKEN"
        )
    auth = None
    if token:
        scopes = [
            item.strip()
            for item in _env_value(
                "LLM_WIKI_MCP_TOKEN_SCOPES", "LLM_WIKI_API_TOKEN_SCOPES", "read,write"
            ).split(",")
            if item.strip()
        ]
        auth = StaticTokenVerifier(
            {
                token: {
                    "client_id": "llm-wiki-mcp-client",
                    "scopes": scopes,
                }
            }
        )
    mcp = FastMCP("llm-wiki", auth=auth)
    read_auth = require_scopes("read") if auth is not None else None
    write_auth = require_scopes("write") if auth is not None else None

    @mcp.tool(name="query", auth=read_auth)
    def query(workspace_id: str, query_text: str, session_id: str = "default", mode: str = "deterministic") -> dict[str, object]:
        """Ask a grounded question about existing wiki knowledge."""
        return gateway.call_mcp_tool("query", {"workspace_id": workspace_id, "query_text": query_text, "session_id": session_id, "mode": mode})

    @mcp.tool(name="search", auth=read_auth)
    def search(workspace_id: str, query_text: str, hop_limit: int = 1, max_nodes: int = 40) -> dict[str, object]:
        """Retrieve bounded relevant knowledge and supporting context."""
        return gateway.call_mcp_tool("search", {"workspace_id": workspace_id, "query_text": query_text, "hop_limit": hop_limit, "max_nodes": max_nodes})

    @mcp.tool(name="ingest", auth=write_auth)
    def ingest(workspace_id: str, source_uri: str, raw_text: str | None = None, title: str = "", provenance_policy: str = "optional", provenance: dict[str, Any] | None = None) -> dict[str, object]:
        """Capture a raw source through the canonical ingestion pipeline."""
        return gateway.call_mcp_tool("ingest", {"workspace_id": workspace_id, "source_uri": source_uri, "raw_text": raw_text, "title": title, "provenance_policy": provenance_policy, "provenance": provenance})

    @mcp.tool(name="source", auth=read_auth)
    def source(workspace_id: str, source_uri: str = "", source_document_id: str = "") -> dict[str, object]:
        """Inspect a source by workspace_id plus source_uri or source_document_id."""
        return gateway.call_mcp_tool("source", {"workspace_id": workspace_id, "source_uri": source_uri, "source_document_id": source_document_id})

    @mcp.tool(name="reingest", auth=write_auth)
    def reingest(workspace_id: str, source_uri: str = "", source_document_id: str = "", raw_text: str | None = None, title: str = "", provenance_policy: str | None = None, provenance: dict[str, Any] | None = None) -> dict[str, object]:
        """Update an existing source using source_uri or source_document_id."""
        return gateway.call_mcp_tool("reingest", {"workspace_id": workspace_id, "source_uri": source_uri, "source_document_id": source_document_id, "raw_text": raw_text, "title": title, "provenance_policy": provenance_policy, "provenance": provenance})

    @mcp.tool(name="maintain", auth=write_auth)
    def maintain(workspace_id: str, topic: str = "", objective: str = "", source_document_ids: list[str] | None = None, max_time_seconds: float | None = None, max_llm_calls: int | None = None, max_tokens: int | None = None, max_cost_usd: float | None = None, max_steps: int | None = None) -> dict[str, object]:
        """Queue maintenance for a topic or explicit source_document_ids."""
        return gateway.call_mcp_tool("maintain", {"workspace_id": workspace_id, "topic": topic, "objective": objective, "source_document_ids": source_document_ids, "max_time_seconds": max_time_seconds, "max_llm_calls": max_llm_calls, "max_tokens": max_tokens, "max_cost_usd": max_cost_usd, "max_steps": max_steps})

    @mcp.tool(name="status", auth=read_auth)
    def status(workspace_id: str) -> dict[str, object]:
        """Report wiki health, source state, graph quality, and maintenance state."""
        return gateway.call_mcp_tool("status", {"workspace_id": workspace_id})

    @mcp.tool(name="hypergraph_search", auth=read_auth)
    def hypergraph_search(workspace_id: str, query_text: str = "", hop_limit: int = 1, max_nodes: int = 40, max_edges: int = 80, max_hyperedges: int = 12, include_tombstones: bool = False) -> dict[str, object]:
        """Inspect bounded raw graph and hypergraph structure read-only."""
        return gateway.call_mcp_tool("hypergraph_search", {"workspace_id": workspace_id, "query_text": query_text, "hop_limit": hop_limit, "max_nodes": max_nodes, "max_edges": max_edges, "max_hyperedges": max_hyperedges, "include_tombstones": include_tombstones})

    @mcp.tool(name="history", auth=read_auth)
    def history(workspace_id: str, session_id: str | None = None, limit: int = 100) -> dict[str, object]:
        """Inspect prior investigations, decisions, and grounding metadata."""
        return gateway.call_mcp_tool("history", {"workspace_id": workspace_id, "session_id": session_id, "limit": limit})

    @mcp.tool(name="propose", auth=write_auth)
    def propose(request: dict[str, Any], proposal: dict[str, Any]) -> dict[str, object]:
        """Validate a candidate durable knowledge change without applying it."""
        return gateway.call_mcp_tool("propose", {"request": request, "proposal": proposal})

    @mcp.tool(name="confirm", auth=write_auth)
    def confirm(workspace_id: str, interaction_id: str, confirmed: bool, proposal: dict[str, Any] | None = None) -> dict[str, object]:
        """Explicitly approve and apply a previously validated change."""
        return gateway.call_mcp_tool("confirm", {"workspace_id": workspace_id, "interaction_id": interaction_id, "confirmed": confirmed, "proposal": proposal})

    return mcp


__all__ = ["build_agent_mcp"]
