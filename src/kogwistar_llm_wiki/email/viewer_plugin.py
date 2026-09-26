"""Compatibility adapter for the optional email-plugin browser shell."""

from __future__ import annotations


def render_email_viewer_plugin() -> str:
    """Load the email viewer shell from the optional plugin package."""

    try:
        from kogwistar_email_plugin import render_email_viewer_plugin as render
    except ImportError as exc:
        raise RuntimeError("install kogwistar-email-plugin to enable the email viewer") from exc
    return render()


__all__ = ["render_email_viewer_plugin"]
