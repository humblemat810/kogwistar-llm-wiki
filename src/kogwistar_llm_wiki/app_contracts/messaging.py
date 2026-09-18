"""Typed message envelopes for application-owned lane communication.

Kogwistar remains authoritative for generic queue, lease, and graph contracts.
These models describe the LLM-Wiki application payload carried across its
foreground and background lanes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_extension.model_slicing import BackendType, DtoType, ModeSlicingMixin


class MessageEnvelope(ModeSlicingMixin, BaseModel):
    """A bounded application message with an external DTO view."""

    target: DtoType[Literal["foreground", "background"]] | DtoType[str]
    payload: DtoType[object]
    intent: DtoType[Literal["request", "notification", "alert"]] = "notification"
    provenance_id: DtoType[str | None] = None
    internal_trace_id: BackendType[str | None] = None


class MessageChannel:
    """Create DTO-safe messages for cross-lane application communication."""

    @staticmethod
    def wrap_message(
        payload: object,
        target: Literal["foreground", "background"] | str,
        intent: Literal["request", "notification", "alert"] = "notification",
        provenance_id: str | None = None,
    ) -> dict[str, object]:
        """Validate and return the public DTO slice of a message envelope."""

        envelope = MessageEnvelope(
            target=target,
            payload=payload,
            intent=intent,
            provenance_id=provenance_id,
        )
        return MessageEnvelope["dto"](
            target=envelope.target,
            payload=envelope.payload,
            intent=envelope.intent,
            provenance_id=envelope.provenance_id,
        ).model_dump()
