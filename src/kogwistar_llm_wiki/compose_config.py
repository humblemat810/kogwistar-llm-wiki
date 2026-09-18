"""Compatibility facade for Compose generation and validation."""

from pathlib import Path

from .compose.options import ComposeConfigurationError, ComposeOptions, validate_options
from .compose.rendering import render_compose
from .compose.validation import check_compose_text
from .compose.validation import write_compose as _write_compose


def write_compose(path: str | Path, options: ComposeOptions) -> Path:
    """Write Compose text while preserving the historical root API."""
    return _write_compose(path, options, render=render_compose)


__all__ = [
    "ComposeConfigurationError",
    "ComposeOptions",
    "check_compose_text",
    "render_compose",
    "validate_options",
    "write_compose",
]
