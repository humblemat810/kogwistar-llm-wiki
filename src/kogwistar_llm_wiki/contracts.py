"""Backward-compatible import shim for application contracts.

New code should import from :mod:`kogwistar_llm_wiki.app_contracts`. This
module remains stable for consumers that already import ``contracts``.
"""

from .app_contracts.messaging import MessageChannel, MessageEnvelope

__all__ = ["MessageChannel", "MessageEnvelope"]
