"""One grounded interaction contract behind agent-facing protocols."""

from __future__ import annotations

from dataclasses import dataclass
import time
import uuid
from collections.abc import Mapping
from typing import Any

from .otel import LlmWikiTelemetry
from .workbench_api import WorkbenchApi


@dataclass(frozen=True, slots=True)
class AgentTurn:
    request_id: str
    model: str
    response: dict[str, object]
    status: str = "completed"


class AgentGateway:
    """Normalizes agent protocols without bypassing WorkbenchApi safeguards."""

    def __init__(self, api: WorkbenchApi, *, telemetry: LlmWikiTelemetry | None = None) -> None:
        self.api = api
        self.telemetry = telemetry or LlmWikiTelemetry.from_environment()

    def responses(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request_id = _request_id(payload, "resp")
        model = str(payload.get("model") or "llm-wiki-deterministic")
        with self.telemetry.span("llm_wiki.responses", {"request_id": request_id, "model": model}):
            result = self._answer(payload)
        answer = _answer_text(result)
        return {
            "id": request_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": model,
            "output": [{
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": answer, "annotations": []}],
            }],
            "usage": None,
            "llm_wiki": result,
        }

    def chat_completions(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request_id = _request_id(payload, "chatcmpl")
        model = str(payload.get("model") or "llm-wiki-deterministic")
        with self.telemetry.span("llm_wiki.chat_completions", {"request_id": request_id, "model": model}):
            result = self._answer(payload)
        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": _answer_text(result)},
                "finish_reason": "stop",
            }],
            "usage": None,
            "llm_wiki": result,
        }

    def a2a_message(self, payload: Mapping[str, Any]) -> dict[str, object]:
        """Submit or execute an A2A-style message using durable interactions."""
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        request = _request_payload(payload)
        request.update({str(k): v for k, v in metadata.items()})
        if bool(payload.get("background", True)) and self.api.dispatcher is not None:
            interaction = self.api.submit_interaction(request)
            return _a2a_task(interaction)
        result = self._answer(request)
        task_id = _request_id(payload, "task")
        return {"id": task_id, "status": {"state": "completed"}, "artifacts": [{"parts": [{"kind": "text", "text": _answer_text(result)}]}], "metadata": {"llm_wiki": result}}

    def a2a_task(self, *, workspace_id: str, task_id: str) -> dict[str, object] | None:
        interaction = self.api.get_interaction(workspace_id=workspace_id, interaction_id=task_id)
        if interaction is None:
            return None
        return _a2a_task(interaction)

    def mcp_tool_names(self) -> tuple[str, ...]:
        return ("llm_wiki.ask", "llm_wiki.search", "llm_wiki.history", "llm_wiki.propose", "llm_wiki.confirm")

    def call_mcp_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, object]:
        with self.telemetry.span("llm_wiki.mcp_tool", {"tool": name}):
            if name == "llm_wiki.ask":
                return self._answer(arguments)
            if name == "llm_wiki.search":
                return self.api.get_lens(arguments)
            if name == "llm_wiki.history":
                return {"records": self.api.get_history(workspace_id=str(arguments["workspace_id"]), session_id=str(arguments.get("session_id") or "") or None, limit=int(arguments.get("limit", 100)))}
            if name == "llm_wiki.propose":
                return self.api.validate_proposal(arguments)
            if name == "llm_wiki.confirm":
                return self.api.confirm_cockpit_proposal(arguments)
            raise ValueError(f"unknown MCP tool: {name}")

    def _answer(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request = _request_payload(payload)
        return self.api.ask(request)


def _request_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        text = next((_content_to_text(item.get("content")) for item in reversed(messages) if isinstance(item, Mapping) and item.get("role") == "user"), "")
    else:
        value = payload.get("input", payload.get("query_text", ""))
        if isinstance(payload.get("message"), Mapping):
            parts = payload["message"].get("parts")
            if isinstance(parts, list):
                value = "\n".join(_content_to_text(part) for part in parts)
        if isinstance(value, list):
            text = "\n".join(_content_to_text(item.get("content", "")) for item in value if isinstance(item, Mapping))
        else:
            text = _content_to_text(value)
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    request: dict[str, object] = {"workspace_id": str(payload.get("workspace_id") or metadata.get("workspace_id") or "default"), "query_text": text, "session_id": str(payload.get("session_id") or metadata.get("session_id") or "default"), "mode": str(payload.get("mode") or metadata.get("mode") or "deterministic")}
    for key in ("graph_spaces", "semantic_retrieval", "explicit_anchor_ids", "hop_limit", "max_nodes", "max_edges", "max_hyperedges", "pinned_node_ids", "source_watermark", "include_tombstones", "interaction_id"):
        if key in payload:
            request[key] = payload[key]
    return request


def _content_to_text(value: object) -> str:
    """Extract text from OpenAI/A2A content parts without leaking Python reprs."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "text" in value:
            return _content_to_text(value["text"])
        if "content" in value:
            return _content_to_text(value["content"])
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(
            text for text in (_content_to_text(item) for item in value) if text
        )
    return "" if value is None else str(value)


def _answer_text(result: Mapping[str, Any]) -> str:
    answer = result.get("answer")
    return str(answer.get("text") if isinstance(answer, Mapping) else answer or "")


def _request_id(payload: Mapping[str, Any], prefix: str) -> str:
    return str(payload.get("id") or f"{prefix}_{uuid.uuid4().hex}")


def _a2a_task(interaction: Mapping[str, Any]) -> dict[str, object]:
    status = str(interaction.get("status") or "pending")
    state = {"pending": "working", "completed": "completed", "failed": "failed"}.get(status, status)
    response = interaction.get("response")
    text = _answer_text(response) if isinstance(response, Mapping) else ""
    result: dict[str, object] = {"id": interaction.get("interaction_id"), "status": {"state": state}, "metadata": {"workspace_id": interaction.get("workspace_id")}}
    if text:
        result["artifacts"] = [{"parts": [{"kind": "text", "text": text}]}]
    if interaction.get("error"):
        result["status"] = {"state": "failed", "message": {"messageId": interaction.get("interaction_id"), "parts": [{"kind": "text", "text": str(interaction["error"])}]}}
    return result


__all__ = ["AgentGateway", "AgentTurn"]
