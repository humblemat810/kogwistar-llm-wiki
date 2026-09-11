"""Configuration for one immutable Qwen3-VL service profile."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os

from llm_wiki_representation_contract import EmbeddingProfile

DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
MIN_DIMENSION = 64
MAX_DIMENSION = 2048
TORCH_BACKENDS = {"cpu", "cu126", "cu128"}


@dataclass(frozen=True, slots=True)
class RepresentationServiceConfig:
    model: str = DEFAULT_MODEL
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
        if not self.revision or not self.revision.strip():
            raise ValueError("LLM_WIKI_REPRESENTATION_MODEL_REVISION is required and must be immutable; set a pinned revision")
        if not MIN_DIMENSION <= self.dimension <= MAX_DIMENSION:
            raise ValueError("representation dimension must be between 64 and 2048")
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("representation device must be cpu or cuda")
        if self.torch_backend not in TORCH_BACKENDS:
            raise ValueError("representation torch backend must be cpu, cu126, or cu128")
        if self.device == "cuda" and self.torch_backend == "cpu":
            raise ValueError("CUDA representation device requires a CUDA Torch backend")
        if self.batch_size <= 0 or self.max_items <= 0 or self.max_request_bytes <= 0:
            raise ValueError("representation service limits must be positive")

    @property
    def profile(self) -> EmbeddingProfile:
        return EmbeddingProfile(
            provider="transformers",
            model=self.model,
            model_revision=self.revision,
            representation="dense",
            dimension=self.dimension,
            metric="dot",
            preprocessing_fingerprint=(
                f"qwen3-vl:dense:768:32768:{sha256(self.instruction.encode('utf-8')).hexdigest()[:16]}"
            ),
        )


def _env(values: dict[str, str], name: str, default: str) -> str:
    return values.get(name, default).strip() or default


def load_config(environ: dict[str, str] | None = None) -> RepresentationServiceConfig:
    values = environ if environ is not None else os.environ
    revision = values.get("LLM_WIKI_REPRESENTATION_MODEL_REVISION", "").strip()
    return RepresentationServiceConfig(
        model=_env(values, "LLM_WIKI_REPRESENTATION_MODEL", DEFAULT_MODEL),
        revision=revision or None,
        dimension=int(_env(values, "LLM_WIKI_REPRESENTATION_DIMENSION", "1024")),
        device=_env(values, "LLM_WIKI_REPRESENTATION_DEVICE", "cpu").lower(),
        torch_backend=_env(values, "LLM_WIKI_REPRESENTATION_TORCH_BACKEND", "cpu").lower(),
        batch_size=int(_env(values, "LLM_WIKI_REPRESENTATION_BATCH_SIZE", "1")),
        token=values.get("LLM_WIKI_REPRESENTATION_TOKEN") or None,
        max_request_bytes=int(_env(values, "LLM_WIKI_REPRESENTATION_MAX_REQUEST_BYTES", "5000000")),
        max_items=int(_env(values, "LLM_WIKI_REPRESENTATION_MAX_ITEMS", "32")),
        model_cache_dir=values.get("HF_HOME") or None,
        instruction=_env(values, "LLM_WIKI_REPRESENTATION_INSTRUCTION", "Represent the user's input."),
    )


__all__ = ["RepresentationServiceConfig", "load_config", "DEFAULT_MODEL", "MIN_DIMENSION", "MAX_DIMENSION"]
