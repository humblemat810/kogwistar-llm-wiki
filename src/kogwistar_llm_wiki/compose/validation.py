"""Dependency-free validation and file output for generated Compose text."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .options import ComposeOptions


def check_compose_text(text: str) -> dict[str, object]:
    """Perform dependency-free structural checks on generated or hand-written YAML."""
    checks: dict[str, str] = {}
    errors: list[str] = []
    required = ("services:", "llm-wiki:")
    for token in required:
        checks[token] = "ok" if token in text else "missing"
        if token not in text:
            errors.append(f"missing required Compose element: {token}")
    if "postgres:" in text:
        for token in ("KOGWISTAR_POSTGRES_DSN", "healthcheck:"):
            checks[token] = "ok" if token in text else "missing"
            if token not in text:
                errors.append(f"missing required PostgreSQL element: {token}")
    if "LLM_WIKI_EMBEDDING_DEVICE: cuda" in text and "driver: nvidia" not in text:
        errors.append("CUDA embedding service requires an NVIDIA device reservation")
    if "Qwen3-VL-Embedding-2B" in text and not any(
        name in text
        for name in ("LLM_WIKI_MULTIMODAL_MODEL_REVISION", "LLM_WIKI_EMBEDDING_MODEL_REVISION")
    ):
        errors.append("Qwen3-VL embedding requires an immutable model revision")
    return {"valid": not errors, "errors": errors, "checks": checks}


def write_compose(
    path: str | Path,
    options: ComposeOptions,
    *,
    render: Callable[[ComposeOptions], str] | None = None,
) -> Path:
    """Write rendered Compose text while keeping rendering policy injectable."""
    if render is None:
        from .rendering import render_compose

        render = render_compose
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(options), encoding="utf-8")
    return destination
