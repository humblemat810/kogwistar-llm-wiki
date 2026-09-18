"""OpenAI-compatible and A2A protocol adapters for the agent gateway."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from typing import Any

from .gateway_protocol import (
    a2a_task as _a2a_task,
)
from .gateway_protocol import (
    answer_text as _answer_text,
)
from .gateway_protocol import (
    jsonrpc_error as _jsonrpc_error,
)
from .gateway_protocol import (
    jsonrpc_result as _jsonrpc_result,
)
from .gateway_protocol import (
    request_id as _request_id,
)
from .protocol import request_payload as _request_payload


class AgentProtocolMixin:
    """Expose protocol-specific envelopes over the shared gateway answer."""

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
        with self.telemetry.span(
            "llm_wiki.chat_completions",
            {"request_id": request_id, "model": model},
        ):
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
        return {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": _answer_text(result)}]}],
            "metadata": {"llm_wiki": result},
        }

    def a2a_task(self, *, workspace_id: str, task_id: str) -> dict[str, object] | None:
        interaction = self.api.get_interaction(workspace_id=workspace_id, interaction_id=task_id)
        if interaction is None:
            return None
        return _a2a_task(interaction)

    def a2a_jsonrpc(self, payload: Mapping[str, Any]) -> dict[str, object]:
        """Handle the A2A JSON-RPC binding without duplicating task logic."""
        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0":
            return _jsonrpc_error(request_id, -32600, "Invalid Request", "jsonrpc must be '2.0'")
        method = payload.get("method")
        params = payload.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, Mapping):
            return _jsonrpc_error(request_id, -32600, "Invalid Request", "method and params are invalid")
        try:
            if method == "message/send":
                return _jsonrpc_result(request_id, _a2a_task(self.a2a_message(params), standard=True))
            if method == "tasks/get":
                metadata = params.get("metadata") if isinstance(params.get("metadata"), Mapping) else {}
                workspace_id = str(params.get("workspace_id") or metadata.get("workspace_id") or "default")
                task_id = str(params.get("id") or params.get("taskId") or "")
                if not task_id:
                    return _jsonrpc_error(request_id, -32602, "Invalid params", "tasks/get requires id")
                task = self.a2a_task(workspace_id=workspace_id, task_id=task_id)
                if task is None:
                    return _jsonrpc_error(request_id, -32001, "Task not found", {"taskId": task_id})
                return _jsonrpc_result(request_id, _a2a_task(task, standard=True))
            if method == "message/stream":
                return _jsonrpc_result(request_id, _a2a_task(self.a2a_message(params), standard=True))
            return _jsonrpc_error(request_id, -32601, "Method not found", method)
        except (KeyError, TypeError, ValueError) as exc:
            return _jsonrpc_error(request_id, -32602, "Invalid params", str(exc))
