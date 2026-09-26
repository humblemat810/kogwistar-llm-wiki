"""Typed options and validation for generated Compose bundles."""

from __future__ import annotations

import re
from dataclasses import dataclass


class ComposeConfigurationError(ValueError):
    """The requested deployment cannot be generated safely."""


@dataclass(frozen=True, slots=True)
class ComposeOptions:
    backend: str = "postgres"
    workspace: str = "default"
    project_name: str = "llm-wiki"
    mode: str = "gpu"
    embedding_backend: str = "auto"
    with_otel: bool = False
    with_oauth: bool = False
    auth_mode: str = "disabled"
    model_revision: str = ""
    embedding_dimension: int = 1024
    # Defaults match the validated Ovis Omni vLLM profile. Operators selecting
    # another model must provide its tested context and memory settings.
    embedding_max_model_len: int = 512
    embedding_crop_token_budget: int = 480
    embedding_gpu_memory_utilization: float = 0.80
    embedding_vllm_enforce_eager: bool = True
    embedding_vllm_max_num_seqs: int = 1
    postgres_password: str = "change-this-development-password"


def validate_options(options: ComposeOptions) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", options.workspace):
        errors.append("workspace must contain only letters, numbers, '.', '_', or '-'")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", options.project_name):
        errors.append("project_name must contain only letters, numbers, '.', '_', or '-'")
    if options.mode not in {"cpu", "gpu", "text-only"}:
        errors.append("mode must be cpu, gpu, or text-only")
    if options.embedding_backend not in {"auto", "vllm", "transformers"}:
        errors.append("embedding_backend must be auto, vllm, or transformers")
    if options.mode == "text-only" and options.embedding_backend != "auto":
        errors.append("embedding_backend is only valid for cpu or gpu multimodal modes")
    if options.mode == "cpu" and options.embedding_backend == "vllm":
        errors.append("vllm embedding_backend is GPU-only; use transformers for CPU")
    if options.backend not in {"postgres", "chroma", "pinecone", "qdrant"}:
        errors.append("backend must be postgres, chroma, pinecone, or qdrant")
    elif options.backend == "chroma":
        errors.append(
            "embedded Chroma cannot be generated for the multi-process Compose bundle; "
            "use PostgreSQL or the single-process demo"
        )
    if options.auth_mode not in {"disabled", "static_token", "kogwistar_jwt"}:
        errors.append("auth_mode must be disabled, static_token, or kogwistar_jwt")
    if options.mode in {"cpu", "gpu"} and not options.model_revision.strip():
        errors.append("model_revision is required for the multimodal service")
    if not 64 <= options.embedding_dimension <= 2048:
        errors.append("embedding_dimension must be between 64 and 2048")
    if options.backend == "postgres" and options.mode == "gpu" and options.embedding_dimension > 1536:
        errors.append("GPU pgvector default profile cannot use dimensions above 1536")
    if options.embedding_max_model_len <= 0:
        errors.append("embedding_max_model_len must be positive")
    elif options.embedding_max_model_len > 8192:
        errors.append("embedding_max_model_len cannot exceed 8192")
    if options.embedding_crop_token_budget <= 0:
        errors.append("embedding_crop_token_budget must be positive")
    elif options.embedding_crop_token_budget > options.embedding_max_model_len:
        errors.append("embedding_crop_token_budget cannot exceed embedding_max_model_len")
    if not 0 < options.embedding_gpu_memory_utilization <= 1:
        errors.append("embedding_gpu_memory_utilization must be between 0 and 1")
    if options.embedding_vllm_max_num_seqs <= 0:
        errors.append("embedding_vllm_max_num_seqs must be positive")
    return errors
