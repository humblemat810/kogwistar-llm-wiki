"""Runtime validation for the opt-in native multimodal Torch profiles."""

from __future__ import annotations

import importlib
import os
from typing import Literal, cast


TorchBackend = Literal["none", "cpu", "cu126", "cu128"]
MultimodalBackend = Literal["none", "transformers", "legacy-colqwen"]
SUPPORTED_TORCH_BACKENDS: tuple[TorchBackend, ...] = ("none", "cpu", "cu126", "cu128")
SUPPORTED_MULTIMODAL_BACKENDS: tuple[MultimodalBackend, ...] = (
    "none", "transformers", "legacy-colqwen"
)
DEFAULT_MULTIMODAL_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
DEFAULT_MULTIMODAL_DIMENSION = 1024
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


def configured_representation_service_url(
    environ: dict[str, str] | None = None,
) -> str | None:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_REPRESENTATION_SERVICE_URL", "").strip()
    return value or None


def configured_representation_service_token(
    environ: dict[str, str] | None = None,
) -> str | None:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_REPRESENTATION_SERVICE_TOKEN", "").strip()
    return value or None


def configured_representation_service_timeout(
    environ: dict[str, str] | None = None,
) -> float:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_REPRESENTATION_SERVICE_TIMEOUT_SECONDS", "30").strip()
    try:
        timeout = float(value)
    except ValueError as exc:
        raise ValueError(
            "LLM_WIKI_REPRESENTATION_SERVICE_TIMEOUT_SECONDS must be a number"
        ) from exc
    if timeout <= 0:
        raise ValueError("LLM_WIKI_REPRESENTATION_SERVICE_TIMEOUT_SECONDS must be positive")
    return timeout


def configured_representation_service_max_request_bytes(
    environ: dict[str, str] | None = None,
) -> int:
    values = environ if environ is not None else os.environ
    value = values.get("LLM_WIKI_REPRESENTATION_SERVICE_MAX_REQUEST_BYTES", "5000000").strip()
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError(
            "LLM_WIKI_REPRESENTATION_SERVICE_MAX_REQUEST_BYTES must be an integer"
        ) from exc
    if limit <= 0:
        raise ValueError("LLM_WIKI_REPRESENTATION_SERVICE_MAX_REQUEST_BYTES must be positive")
    return limit


def configured_representation_service_allowed_hosts(
    environ: dict[str, str] | None = None,
) -> tuple[str, ...]:
    values = environ if environ is not None else os.environ
    return tuple(
        host.strip().lower()
        for host in values.get("LLM_WIKI_REPRESENTATION_SERVICE_ALLOWED_HOSTS", "").split(",")
        if host.strip()
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
