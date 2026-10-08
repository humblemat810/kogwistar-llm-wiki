"""MCP capability names, descriptions, and dispatch for the agent gateway."""

from __future__ import annotations

from .host import AgentGatewayHost, JsonObject, ToolArguments


class AgentToolCatalogMixin(AgentGatewayHost):
    def mcp_tool_names(self) -> tuple[str, ...]:
        return (
            "query",
            "search",
            "ingest",
            "source",
            "reingest",
            "maintain",
            "status",
            "hypergraph_search",
            "history",
            "memory_recall",
            "memory_capture",
            "memory_review",
            "multimodal_capture",
            "multimodal_index",
            "multimodal_search",
            "multimodal_status",
            "propose",
            "confirm",
        )

    def mcp_tool_descriptions(self) -> dict[str, str]:
        return {
            "query": "Ask a grounded question about existing wiki knowledge.",
            "search": "Retrieve bounded relevant knowledge and supporting context.",
            "ingest": "Capture a raw source through the canonical ingestion pipeline.",
            "source": "Inspect a source using workspace_id plus source_uri or source_document_id.",
            "reingest": "Update an existing source using workspace_id plus source_uri or source_document_id.",
            "maintain": "Queue maintenance using workspace_id plus a topic or source_document_ids.",
            "status": "Report wiki health, source state, graph quality, and maintenance state.",
            "hypergraph_search": "Inspect bounded raw graph and hypergraph structure read-only.",
            "history": "Inspect prior investigations, decisions, and grounding metadata.",
            "memory_recall": "Recall bounded, evidence-backed project memory for relevant work.",
            "memory_capture": "Capture a structured, evidence-backed project memory record.",
            "memory_review": "Review project memory records, evidence, lifecycle, and conflicts.",
            "multimodal_capture": "Capture revision-bound multimodal projection units for later indexing.",
            "multimodal_index": "Promote pending multimodal units through the configured embedding profile.",
            "multimodal_search": "Search authorized multimodal projection evidence by text.",
            "multimodal_status": "Report multimodal projection readiness, profile, and stage counts.",
            "propose": "Validate a candidate durable knowledge change without applying it.",
            "confirm": "Explicitly approve and apply a previously validated knowledge change.",
        }

    def call_mcp_tool(self, name: str, arguments: ToolArguments) -> JsonObject:
        with self.telemetry.span("llm_wiki.mcp_tool", {"tool": name}):
            handlers = {
                "query": self.query,
                "search": self.search,
                "ingest": self.ingest,
                "source": self.source,
                "reingest": self.reingest,
                "maintain": self.maintain,
                "status": self.status,
                "hypergraph_search": self.hypergraph_search,
                "history": self.history,
                "memory_recall": self.memory_recall,
                "memory_capture": self.memory_capture,
                "memory_review": self.memory_review,
                "multimodal_capture": self.multimodal_capture,
                "multimodal_index": self.multimodal_index,
                "multimodal_search": self.multimodal_search,
                "multimodal_status": self.multimodal_status,
                "propose": self.propose,
                "confirm": self.confirm,
            }
            handler = handlers.get(name)
            if handler is not None:
                return handler(arguments)
            raise ValueError(f"unknown MCP tool: {name}")
