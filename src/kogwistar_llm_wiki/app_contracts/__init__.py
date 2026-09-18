"""Application-owned contracts that cross LLM-Wiki subsystem boundaries."""

from .messaging import MessageChannel, MessageEnvelope

__all__ = ["MessageChannel", "MessageEnvelope"]
