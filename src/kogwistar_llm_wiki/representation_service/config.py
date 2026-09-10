"""Environment configuration for the isolated representation service."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os

from ..multimodal_projection import (
    DEFAULT_QWEN3_VL_MODEL,
    MultimodalEmbeddingProfile,
    QWEN3_VL_MAX_DIMENSION,
    QWEN3_VL_MIN_DIMENSION,
)


@dataclass(frozen=True, slots=True)
class RepresentationServiceConfig:
    model: str = DEFAULT_QWEN3_VL_MODEL
    revision: str | None = None
    dimension: int = 1024
    device: str = "cpu"
    torch_backend: str = "cpu"
    batch_size: int = 1
    token: str | None = None
    max_request_bytes: int = 5_000_000
    max_items: int = 32
    model_cache_dir: str | None = None
    instruction: str = "Represent the user's input."

    def __post_init__(self) -> None:
        if not QWEN3_VL_MIN_DIMENSION <= self.dimension <= QWEN3_VL_MAX_DIMENSION:
            raise ValueError("representation dimension must be between 64 and 2048")
        if self.batch_size <= 0 or self.max_items <= 0 or self.max_request_bytes <= 0:
            raise ValueError("representation service limits must be positive")
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("representation device must be cpu or cuda")
        if self.torch_backend not in {"cpu", "cu126", "cu128"}:
            raise ValueError("representation torch backend must be cpu, cu126, or cu128")
        if self.device == "cuda" and self.torch_backend == "cpu":
            raise ValueError("CUDA representation device requires a CUDA Torch backend")

    @property
    def profile(self) -> MultimodalEmbeddingProfile:
        return MultimodalEmbeddingProfile(
            provider="transformers",
            model=self.model,
            model_revision=self.revision,
            representation="dense",
            dimension=self.dimension,
            metric="dot",
            preprocessing_fingerprint=(
                f"qwen3-vl:dense:768:32768:"
                f"{sha256(self.instruction.encode('utf-8')).hexdigest()[:16]}"
            ),
        )


def _env(values: dict[str, str], name: str, default: str) -> str:
    return values.get(name, default).strip() or default


def load_config(environ: dict[str, str] | None = None) -> RepresentationServiceConfig:
    values = environ if environ is not None else os.environ
    raw_dimension = _env(values, "LLM_WIKI_REPRESENTATION_DIMENSION", "1024")
    try:
        dimension = int(raw_dimension)
    except ValueError as exc:
        raise ValueError("LLM_WIKI_REPRESENTATION_DIMENSION must be an integer") from exc
    raw_items = _env(values, "LLM_WIKI_REPRESENTATION_MAX_ITEMS", "32")
    raw_bytes = _env(values, "LLM_WIKI_REPRESENTATION_MAX_REQUEST_BYTES", "5000000")
    try:
        max_items = int(raw_items)
        max_bytes = int(raw_bytes)
    except ValueError as exc:
        raise ValueError("representation request limits must be integers") from exc
    return RepresentationServiceConfig(
        model=_env(values, "LLM_WIKI_REPRESENTATION_MODEL", DEFAULT_QWEN3_VL_MODEL),
        revision=values.get("LLM_WIKI_REPRESENTATION_MODEL_REVISION") or None,
        dimension=dimension,
        device=_env(values, "LLM_WIKI_REPRESENTATION_DEVICE", "cpu").lower(),
        torch_backend=_env(values, "LLM_WIKI_REPRESENTATION_TORCH_BACKEND", "cpu").lower(),
        batch_size=int(_env(values, "LLM_WIKI_REPRESENTATION_BATCH_SIZE", "1")),
        token=values.get("LLM_WIKI_REPRESENTATION_TOKEN") or None,
        max_request_bytes=max_bytes,
        max_items=max_items,
        model_cache_dir=values.get("HF_HOME") or None,
        instruction=_env(
            values,
            "LLM_WIKI_REPRESENTATION_INSTRUCTION",
            "Represent the user's input.",
        ),
    )


__all__ = ["RepresentationServiceConfig", "load_config"]
