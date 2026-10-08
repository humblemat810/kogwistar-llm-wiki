"""Small structural contracts shared by workbench services."""

from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    """Return the current wall-clock time in milliseconds."""

    def __call__(self, /) -> int: ...


__all__ = ["Clock"]
