"""Configuration for one immutable Qwen3-VL service profile."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from llm_wiki_embedding_contract import EmbeddingProfile

DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
CLIP_MODEL = "sentence-transformers/clip-ViT-B-32"
CLIP_REVISION = "327ab6726d33c0e22f920c83f2ff9e4bd38ca37f"
CLIP_DIMENSION = 512
CLIP_MODEL_SHA256 = "99d28a652e6ec46629ab7047a0ac82c69b1fe11e0ce672c43af65d3a9a3fc05d"
MIN_DIMENSION = 64
MAX_DIMENSION = 2048
TORCH_BACKENDS = {"cpu", "cu126", "cu128"}


@dataclass(frozen=True, slots=True)
class EmbeddingServiceConfig:
    model: str = DEFAULT_MODEL
    encoder: Literal["qwen3-vl", "clip-vit-b32"] = "qwen3-vl"
    model_path: str | None = None
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
            raise ValueError("LLM_WIKI_EMBEDDING_MODEL_REVISION is required and must be immutable; set a pinned revision")
        if not MIN_DIMENSION <= self.dimension <= MAX_DIMENSION:
            raise ValueError("embedding dimension must be between 64 and 2048")
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("embedding device must be cpu or cuda")
        if self.encoder not in {"qwen3-vl", "clip-vit-b32"}:
            raise ValueError("embedding encoder must be qwen3-vl or clip-vit-b32")
        if self.encoder == "clip-vit-b32" and self.dimension != CLIP_DIMENSION:
            raise ValueError(f"CLIP ViT-B/32 projection dimension must be {CLIP_DIMENSION}")
        if self.torch_backend not in TORCH_BACKENDS:
            raise ValueError("embedding torch backend must be cpu, cu126, or cu128")
        if self.device == "cuda" and self.torch_backend == "cpu":
            raise ValueError("CUDA embedding device requires a CUDA Torch backend")
        if self.batch_size <= 0 or self.max_items <= 0 or self.max_request_bytes <= 0:
            raise ValueError("embedding service limits must be positive")

    @property
    def profile(self) -> EmbeddingProfile:
        if self.encoder == "clip-vit-b32":
            return EmbeddingProfile(
                provider="transformers",
                model=self.model,
                model_revision=self.revision,
                embedding="dense",
                dimension=self.dimension,
                metric="dot",
                preprocessing_fingerprint="clip-vit-b32:shared-text-image-projections-v1",
                max_sequence_length=77,
                max_image_patches=1,
            )
        return EmbeddingProfile(
            provider="transformers",
            model=self.model,
            model_revision=self.revision,
            embedding="dense",
            dimension=self.dimension,
            metric="dot",
            preprocessing_fingerprint=(
                f"qwen3-vl:dense:768:32768:{sha256(self.instruction.encode('utf-8')).hexdigest()[:16]}"
            ),
        )


def _env(values: dict[str, str], name: str, default: str) -> str:
    return values.get(name, default).strip() or default


def _embedding_env(values: dict[str, str], name: str, default: str) -> str:
    """Read one canonical Embedding Service setting."""
    return _env(values, name, default)


def load_config(environ: dict[str, str] | None = None) -> EmbeddingServiceConfig:
    values = environ if environ is not None else os.environ
    revision = values.get("LLM_WIKI_EMBEDDING_MODEL_REVISION", "").strip()
    encoder = _embedding_env(values, "LLM_WIKI_EMBEDDING_ENCODER", "qwen3-vl").lower()
    default_model = CLIP_MODEL if encoder == "clip-vit-b32" else DEFAULT_MODEL
    default_dimension = str(CLIP_DIMENSION if encoder == "clip-vit-b32" else 1024)
    return EmbeddingServiceConfig(
        model=_embedding_env(values, "LLM_WIKI_EMBEDDING_MODEL", default_model),
        encoder=encoder,
        model_path=values.get("LLM_WIKI_EMBEDDING_MODEL_PATH") or None,
        revision=revision or None,
        dimension=int(_embedding_env(values, "LLM_WIKI_EMBEDDING_DIMENSION", default_dimension)),
        device=_embedding_env(values, "LLM_WIKI_EMBEDDING_DEVICE", "cpu").lower(),
        torch_backend=_embedding_env(values, "LLM_WIKI_EMBEDDING_TORCH_BACKEND", "cpu").lower(),
        batch_size=int(_embedding_env(values, "LLM_WIKI_EMBEDDING_BATCH_SIZE", "1")),
        token=values.get("LLM_WIKI_EMBEDDING_TOKEN") or None,
        max_request_bytes=int(_embedding_env(values, "LLM_WIKI_EMBEDDING_MAX_REQUEST_BYTES", "5000000")),
        max_items=int(_embedding_env(values, "LLM_WIKI_EMBEDDING_MAX_ITEMS", "32")),
        model_cache_dir=values.get("HF_HOME") or None,
        instruction=_embedding_env(values, "LLM_WIKI_EMBEDDING_INSTRUCTION", "Represent the user's input."),
    )


__all__ = [
    "CLIP_DIMENSION",
    "CLIP_MODEL",
    "CLIP_MODEL_SHA256",
    "CLIP_REVISION",
    "DEFAULT_MODEL",
    "MAX_DIMENSION",
    "MIN_DIMENSION",
    "EmbeddingServiceConfig",
    "load_config",
]
