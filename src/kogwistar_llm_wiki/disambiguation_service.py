"""Compatibility façade for the application disambiguation service."""

from .disambiguation.service import (
    DisambiguationAnswerRecord,
    DisambiguationAnswerSource,
    DisambiguationService,
)

__all__ = [
    "DisambiguationAnswerRecord",
    "DisambiguationAnswerSource",
    "DisambiguationService",
]
