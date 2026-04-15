from __future__ import annotations
from contextlib import contextmanager
from typing import Any


@contextmanager
def _temporary_namespace(engine: Any, namespace: str):
    """Context manager to temporarily swap an engine's namespace."""
    old_ns = getattr(engine, "namespace", "default")
    engine.namespace = namespace
    try:
        yield
    finally:
        engine.namespace = old_ns
