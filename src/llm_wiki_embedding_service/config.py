"""Configuration for one immutable embedding-service profile."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, cast

from llm_wiki_embedding_contract import EmbeddingProfile

DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
CLIP_MODEL = "sentence-transformers/clip-ViT-B-32"
CLIP_REVISION = "327ab6726d33c0e22f920c83f2ff9e4bd38ca37f"
CLIP_DIMENSION = 512
CLIP_MODEL_SHA256 = "99d28a652e6ec46629ab7047a0ac82c69b1fe11e0ce672c43af65d3a9a3fc05d"
BGE_SMALL_MODEL = "BAAI/bge-small-en-v1.5"
BGE_SMALL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
BGE_SMALL_DIMENSION = 384
BGE_SMALL_MAX_SEQUENCE_LENGTH = 512
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
MIN_DIMENSION = 64
MAX_DIMENSION = 2048
TORCH_BACKENDS = {"cpu", "cu126", "cu128"}
EmbeddingEncoder = Literal["qwen3-vl", "clip-vit-b32", "bge-small-en-v1.5"]


@dataclass(frozen=True, slots=True)
class EmbeddingServiceConfig:
    model: str = DEFAULT_MODEL
    encoder: EmbeddingEncoder = "qwen3-vl"
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
        if self.encoder not in {"qwen3-vl", "clip-vit-b32", "bge-small-en-v1.5"}:
            raise ValueError("embedding encoder must be qwen3-vl, clip-vit-b32, or bge-small-en-v1.5")
        if self.encoder == "clip-vit-b32" and self.dimension != CLIP_DIMENSION:
            raise ValueError(f"CLIP ViT-B/32 projection dimension must be {CLIP_DIMENSION}")
        if self.encoder == "bge-small-en-v1.5" and self.dimension != BGE_SMALL_DIMENSION:
            raise ValueError(f"BGE-small projection dimension must be {BGE_SMALL_DIMENSION}")
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
        if self.encoder == "bge-small-en-v1.5":
            return EmbeddingProfile(
                provider="transformers",
                model=self.model,
                model_revision=self.revision,
                embedding="dense",
                dimension=self.dimension,
                metric="dot",
                preprocessing_fingerprint=(
                    "bge-small-en-v1.5:cls-l2:query-prefix-v1:"
                    f"{sha256(BGE_QUERY_INSTRUCTION.encode('utf-8')).hexdigest()[:16]}:"
                    f"{BGE_SMALL_MAX_SEQUENCE_LENGTH}"
                ),
                max_sequence_length=BGE_SMALL_MAX_SEQUENCE_LENGTH,
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


def _env(values: Mapping[str, str], name: str, default: str) -> str:
    return values.get(name, default).strip() or default


def _embedding_env(values: Mapping[str, str], name: str, default: str) -> str:
    """Read one canonical Embedding Service setting."""
    return _env(values, name, default)


def _encoder(value: str) -> EmbeddingEncoder:
    if value in {"qwen3-vl", "clip-vit-b32", "bge-small-en-v1.5"}:
        return cast(EmbeddingEncoder, value)
    raise ValueError(f"unsupported embedding encoder {value!r}")


def load_config(environ: Mapping[str, str] | None = None) -> EmbeddingServiceConfig:
    values = environ if environ is not None else os.environ
    revision = values.get("LLM_WIKI_EMBEDDING_MODEL_REVISION", "").strip()
    encoder = _encoder(_embedding_env(values, "LLM_WIKI_EMBEDDING_ENCODER", "qwen3-vl").lower())
    default_model = {"clip-vit-b32": CLIP_MODEL, "bge-small-en-v1.5": BGE_SMALL_MODEL}.get(
        encoder, DEFAULT_MODEL
    )
    default_dimension = str(
        {"clip-vit-b32": CLIP_DIMENSION, "bge-small-en-v1.5": BGE_SMALL_DIMENSION}.get(
            encoder, 1024
        )
    )
    if not revision and encoder == "bge-small-en-v1.5":
        revision = BGE_SMALL_REVISION
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
    "BGE_QUERY_INSTRUCTION",
    "BGE_SMALL_DIMENSION",
    "BGE_SMALL_MAX_SEQUENCE_LENGTH",
    "BGE_SMALL_MODEL",
    "BGE_SMALL_REVISION",
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
