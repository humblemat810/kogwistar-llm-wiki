from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field
from pydantic_extension.model_slicing import ModeSlicingMixin, DtoType, BackendType


class MessageEnvelope(ModeSlicingMixin, BaseModel):
    target: DtoType[Literal["foreground", "background"]] | DtoType[str]
    payload: DtoType[Any]
    intent: DtoType[Literal["request", "notification", "alert"]] = "notification"
    provenance_id: DtoType[Optional[str]] = None
    
    # Internal metadata not shared with DTO by default if we want
    internal_trace_id: BackendType[Optional[str]] = None


class MessageChannel:
    """
    Generalized message channel helper for cross-lane and cross-system messaging.
    Facilitates future external channel integrations with shape fixing.
    """
    
    @staticmethod
    def wrap_message(
        payload: Any,
        target: Literal["foreground", "background"] | str,
        intent: Literal["request", "notification", "alert"] = "notification",
        provenance_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Wraps a payload into a MessageEnvelope and returns its DTO view.
        """
        envelope = MessageEnvelope(
            target=target,
            payload=payload,
            intent=intent,
            provenance_id=provenance_id
        )
        # Return the DTO slice as a dict for external consumption
        return envelope["dto"](
            target=envelope.target,
            payload=envelope.payload,
            intent=envelope.intent,
            provenance_id=envelope.provenance_id
        ).model_dump()
