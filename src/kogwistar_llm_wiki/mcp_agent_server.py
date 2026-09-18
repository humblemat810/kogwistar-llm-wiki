"""Backward-compatible import for the FastMCP agent transport adapter."""

from .agent.mcp_server import build_agent_mcp

__all__ = ["build_agent_mcp"]
