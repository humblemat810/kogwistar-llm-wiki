"""Small protocol and response-shaping helpers for the agent gateway."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from kogwistar.json_types import JsonValue


def count_job_statuses(jobs: list[Mapping[str, JsonValue]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def answer_text(result: Mapping[str, JsonValue]) -> str:
    answer = result.get("answer")
    return str(answer.get("text") if isinstance(answer, Mapping) else answer or "")


def request_id(payload: Mapping[str, JsonValue], prefix: str) -> str:
    return str(payload.get("id") or f"{prefix}_{uuid.uuid4().hex}")


def a2a_task(interaction: Mapping[str, JsonValue], *, standard: bool = False) -> dict[str, JsonValue]:
    if isinstance(interaction.get("status"), Mapping):
        result = dict(interaction)
        metadata = result.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        result.setdefault("contextId", metadata_map.get("workspace_id", "default"))
        return result
    status = str(interaction.get("status") or "pending")
    state = {
        "pending": "submitted" if standard else "working",
        "completed": "completed",
        "failed": "failed",
    }.get(status, status)
    response = interaction.get("response")
    text = answer_text(response) if isinstance(response, Mapping) else ""
    result: dict[str, JsonValue] = {
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


def jsonrpc_result(request_id: JsonValue, result: JsonValue) -> dict[str, JsonValue]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(
    request_id: JsonValue,
    code: int,
    message: str,
    data: JsonValue | None = None,
) -> dict[str, JsonValue]:
    error: dict[str, JsonValue] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
