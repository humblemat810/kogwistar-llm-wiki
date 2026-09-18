"""Small protocol and response-shaping helpers for the agent gateway."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any


def count_job_statuses(jobs: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def answer_text(result: Mapping[str, Any]) -> str:
    answer = result.get("answer")
    return str(answer.get("text") if isinstance(answer, Mapping) else answer or "")


def request_id(payload: Mapping[str, Any], prefix: str) -> str:
    return str(payload.get("id") or f"{prefix}_{uuid.uuid4().hex}")


def a2a_task(interaction: Mapping[str, Any], *, standard: bool = False) -> dict[str, object]:
    if isinstance(interaction.get("status"), Mapping):
        result = dict(interaction)
        result.setdefault("contextId", (result.get("metadata") or {}).get("workspace_id", "default"))
        return result
    status = str(interaction.get("status") or "pending")
    state = {
        "pending": "submitted" if standard else "working",
        "completed": "completed",
        "failed": "failed",
    }.get(status, status)
    response = interaction.get("response")
    text = answer_text(response) if isinstance(response, Mapping) else ""
    result: dict[str, object] = {
        "id": interaction.get("interaction_id"),
        "contextId": interaction.get("context_id") or interaction.get("workspace_id") or "default",
        "status": {"state": state},
        "metadata": {"workspace_id": interaction.get("workspace_id")},
    }
    if text:
        result["artifacts"] = [{"parts": [{"kind": "text", "text": text}]}]
    if interaction.get("error"):
        result["status"] = {
            "state": "failed",
            "message": {
                "messageId": interaction.get("interaction_id"),
                "parts": [{"kind": "text", "text": str(interaction["error"])}],
            },
        }
    return result


def jsonrpc_result(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(
    request_id: object,
    code: int,
    message: str,
    data: object | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
