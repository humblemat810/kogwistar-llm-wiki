"""Runtime validation for the opt-in native multimodal Torch profiles."""

from __future__ import annotations

import importlib
import os
from typing import Literal, cast


TorchBackend = Literal["none", "cpu", "cu126", "cu128"]
MultimodalBackend = Literal["none", "transformers", "vllm", "legacy-colqwen"]
SUPPORTED_TORCH_BACKENDS: tuple[TorchBackend, ...] = ("none", "cpu", "cu126", "cu128")
SUPPORTED_MULTIMODAL_BACKENDS: tuple[MultimodalBackend, ...] = (
    "none", "transformers", "vllm", "legacy-colqwen"
)
DEFAULT_MULTIMODAL_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
DEFAULT_MULTIMODAL_DIMENSION = 1024
DEFAULT_EMBEDDING_MAX_MODEL_LEN = 8192
DEFAULT_EMBEDDING_CROP_TOKEN_BUDGET = 7680
MAX_EMBEDDING_MODEL_LEN = 8192
TORCH_PUBLIC_VERSION = "2.8.0"
_CUDA_VERSION_BY_BACKEND: dict[TorchBackend, str | None] = {
    "none": None,
    "cpu": None,
    "cu126": "12.6",
    "cu128": "12.8",
}


def configured_torch_backend(environ: dict[str, str] | None = None) -> TorchBackend:
    """Resolve the explicit runtime target without making Torch a base import."""
    value = (environ if environ is not None else os.environ).get(
        "LLM_WIKI_MULTIMODAL_TORCH_BACKEND", "none"
    ).strip().lower()
    if value not in SUPPORTED_TORCH_BACKENDS:
        choices = ", ".join(SUPPORTED_TORCH_BACKENDS)
        raise ValueError(
            f"LLM_WIKI_MULTIMODAL_TORCH_BACKEND must be one of {choices}; got {value!r}."
        )
    return cast(TorchBackend, value)


def configured_multimodal_backend(
    environ: dict[str, str] | None = None,
) -> MultimodalBackend:
    """Resolve the opt-in native model family without importing its runtime."""
    value = (environ if environ is not None else os.environ).get(
        "LLM_WIKI_MULTIMODAL_BACKEND", "none"
    ).strip().lower()
    if value not in SUPPORTED_MULTIMODAL_BACKENDS:
        choices = ", ".join(SUPPORTED_MULTIMODAL_BACKENDS)
        raise ValueError(
            f"LLM_WIKI_MULTIMODAL_BACKEND must be one of {choices}; got {value!r}."
        )
    return cast(MultimodalBackend, value)


def configured_multimodal_model(environ: dict[str, str] | None = None) -> str:
    return (environ if environ is not None else os.environ).get(
        "LLM_WIKI_MULTIMODAL_MODEL", DEFAULT_MULTIMODAL_MODEL
    ).strip() or DEFAULT_MULTIMODAL_MODEL


def configured_multimodal_dimension(environ: dict[str, str] | None = None) -> int:
    value = (environ if environ is not None else os.environ).get(
        "LLM_WIKI_MULTIMODAL_DIMENSION", str(DEFAULT_MULTIMODAL_DIMENSION)
    ).strip()
    try:
        dimension = int(value)
    except ValueError as exc:
        raise ValueError(
            f"LLM_WIKI_MULTIMODAL_DIMENSION must be an integer; got {value!r}."
        ) from exc
    if not 64 <= dimension <= 2048:
        raise ValueError(
            f"LLM_WIKI_MULTIMODAL_DIMENSION must be between 64 and 2048; got {dimension}."
        )
    return dimension


def configured_multimodal_revision(environ: dict[str, str] | None = None) -> str | None:
    value = (environ if environ is not None else os.environ).get(
        "LLM_WIKI_MULTIMODAL_MODEL_REVISION", ""
    ).strip()
    return value or None


def configured_embedding_service_url(
    environ: dict[str, str] | None = None,
) -> str | None:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_EMBEDDING_SERVICE_URL", values.get("LLM_WIKI_EMBEDDING_SERVICE_URL", "")).strip()
    return value or None


def configured_embedding_service_token(
    environ: dict[str, str] | None = None,
) -> str | None:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_EMBEDDING_SERVICE_TOKEN", values.get("LLM_WIKI_EMBEDDING_SERVICE_TOKEN", "")).strip()
    return value or None


def configured_embedding_service_timeout(
    environ: dict[str, str] | None = None,
) -> float:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_EMBEDDING_SERVICE_TIMEOUT_SECONDS", values.get("LLM_WIKI_EMBEDDING_SERVICE_TIMEOUT_SECONDS", "30")).strip()
    try:
        timeout = float(value)
    except ValueError as exc:
        raise ValueError(
            "LLM_WIKI_EMBEDDING_SERVICE_TIMEOUT_SECONDS must be a number"
        ) from exc
    if timeout <= 0:
        raise ValueError("LLM_WIKI_EMBEDDING_SERVICE_TIMEOUT_SECONDS must be positive")
    return timeout


def configured_embedding_service_max_request_bytes(
    environ: dict[str, str] | None = None,
) -> int:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_EMBEDDING_SERVICE_MAX_REQUEST_BYTES", values.get("LLM_WIKI_EMBEDDING_SERVICE_MAX_REQUEST_BYTES", "5000000")).strip()
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError(
            "LLM_WIKI_EMBEDDING_SERVICE_MAX_REQUEST_BYTES must be an integer"
        ) from exc
    if limit <= 0:
        raise ValueError("LLM_WIKI_EMBEDDING_SERVICE_MAX_REQUEST_BYTES must be positive")
    return limit


def configured_embedding_service_allowed_hosts(
    environ: dict[str, str] | None = None,
) -> tuple[str, ...]:
    values = environ if environ is not None else os.environ
    return tuple(
        host.strip().lower()
        for host in values.get("LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS", values.get("LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS", "")).split(",")
        if host.strip()
    )


def configured_vllm_url(environ: dict[str, str] | None = None) -> str | None:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_EMBEDDING_VLLM_URL", "").strip()
    return value or None


def configured_vllm_token(environ: dict[str, str] | None = None) -> str | None:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_EMBEDDING_VLLM_TOKEN", "").strip()
    return value or None


def configured_vllm_image(environ: dict[str, str] | None = None) -> str | None:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_EMBEDDING_VLLM_IMAGE", "").strip()
    return value or None


def configured_vllm_allowed_hosts(
    environ: dict[str, str] | None = None,
) -> tuple[str, ...]:
    values = environ if environ is not None else os.environ
    return tuple(
        host.strip().lower()
        for host in values.get("LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    )


def _positive_int_env(
    environ: dict[str, str] | None, name: str, default: int
) -> int:
    values = environ if environ is not None else os.environ
    value = values.get(name, str(default)).strip()
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def configured_embedding_max_model_len(environ: dict[str, str] | None = None) -> int:
    value = _positive_int_env(
        environ,
        "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN",
        DEFAULT_EMBEDDING_MAX_MODEL_LEN,
    )
    if value > MAX_EMBEDDING_MODEL_LEN:
        raise ValueError(
            "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN cannot exceed 8192"
        )
    return value


def configured_embedding_crop_token_budget(environ: dict[str, str] | None = None) -> int:
    values = environ if environ is not None else os.environ
    limit = configured_embedding_max_model_len(values)
    raw = values.get("LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET")
    budget = (
        min(DEFAULT_EMBEDDING_CROP_TOKEN_BUDGET, limit)
        if raw in {None, ""}
        else _positive_int_env(values, "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET", 0)
    )
    if budget > limit:
        raise ValueError(
            "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET cannot exceed "
            "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN"
        )
    return budget


def configured_embedding_gpu_memory_utilization(
    environ: dict[str, str] | None = None,
) -> float:
    values = environ if environ is not None else os.environ
    raw = values.get("LLM_WIKI_EMBEDDING_GPU_MEMORY_UTILIZATION", "0.86").strip()
    try:
        result = float(raw)
    except ValueError as exc:
        raise ValueError("LLM_WIKI_EMBEDDING_GPU_MEMORY_UTILIZATION must be a number") from exc
    if not 0 < result <= 1:
        raise ValueError("LLM_WIKI_EMBEDDING_GPU_MEMORY_UTILIZATION must be between 0 and 1")
    return result


def configured_embedding_vllm_enforce_eager(
    environ: dict[str, str] | None = None,
) -> bool:
    values = environ if environ is not None else os.environ
    return values.get("LLM_WIKI_EMBEDDING_VLLM_ENFORCE_EAGER", "1").strip().lower() in {
        "1", "true", "yes", "on"
    }


def configured_embedding_vllm_max_num_seqs(
    environ: dict[str, str] | None = None,
) -> int:
    return _positive_int_env(
        environ,
        "LLM_WIKI_EMBEDDING_VLLM_MAX_NUM_SEQS",
        1,
    )


def validate_torch_runtime(backend: TorchBackend, *, require_device: bool) -> dict[str, object] | None:
    """Fail clearly when an explicit profile does not match the installed Torch build."""
    if backend == "none":
        return None
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        raise RuntimeError(
            f"The {backend!r} multimodal runtime was selected but Torch is unavailable. "
            f"Install requirements/multimodal/torch-{backend}.txt, then install "
            f"the .[{ 'multimodal-cpu' if backend == 'cpu' else 'multimodal-cuda' }] extra."
        ) from exc

    version = str(torch.__version__)
    public_version = version.partition("+")[0]
    if public_version != TORCH_PUBLIC_VERSION:
        raise RuntimeError(
            f"The {backend!r} multimodal runtime requires torch {TORCH_PUBLIC_VERSION}, "
            f"but found {version}. Reinstall requirements/multimodal/torch-{backend}.txt "
            f"and the matching multimodal extra."
        )

    cuda_version = getattr(torch.version, "cuda", None)
    expected_cuda = _CUDA_VERSION_BY_BACKEND[backend]
    if expected_cuda is None and cuda_version is not None:
        raise RuntimeError(
            f"The cpu multimodal profile requires a CPU Torch build, but found CUDA {cuda_version}. "
            "Choose cu126/cu128 or recreate the environment with the cpu profile."
        )
    if expected_cuda is not None and cuda_version != expected_cuda:
        found = cuda_version or "a CPU-only build"
        raise RuntimeError(
            f"The {backend!r} multimodal profile requires Torch CUDA {expected_cuda}, but found {found}. "
            f"Reinstall requirements/multimodal/torch-{backend}.txt and the matching "
            ".[multimodal-cuda] extra."
        )

    cuda_available = bool(torch.cuda.is_available())
    if expected_cuda is not None and require_device and not cuda_available:
        raise RuntimeError(
            f"Torch CUDA {cuda_version} is installed but no usable GPU is visible. "
            "Check the NVIDIA driver and container GPU passthrough, or use the cpu profile."
        )
    return {
        "torch_version": version,
        "requested_backend": backend,
        "torch_cuda_version": cuda_version,
        "cuda_available": cuda_available,
        "device_name": torch.cuda.get_device_name(0) if cuda_available else None,
    }


def validate_configured_multimodal_runtime(*, require_device: bool) -> dict[str, object] | None:
    """Validate an explicitly configured profile, preserving Torch-free defaults."""
    return validate_torch_runtime(configured_torch_backend(), require_device=require_device)
