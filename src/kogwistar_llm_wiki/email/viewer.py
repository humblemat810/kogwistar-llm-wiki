"""Compatibility adapter for the optional email-plugin viewer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class EmailViewer:
    """Load the viewer implementation from ``kogwistar-email-plugin``."""

    __slots__ = ("_delegate",)

    def __init__(self, store: Any, *, authorize_stream: Callable[[str, str], bool], review_store: Any = None) -> None:
        try:
            from kogwistar_email_plugin import EmailViewer as PluginEmailViewer
        except ImportError:
            self._delegate = None
        else:
            self._delegate = PluginEmailViewer(
                store,
                authorize_stream=authorize_stream,
                review_store=review_store,
            )

    def get(self, *, workspace_id: str, stream_id: str, source_revision_id: str) -> dict[str, object]:
        if self._delegate is None:
            raise RuntimeError("install kogwistar-email-plugin to enable the email viewer")
        return self._delegate.get(
            workspace_id=workspace_id,
            stream_id=stream_id,
            source_revision_id=source_revision_id,
        )


__all__ = ["EmailViewer"]
