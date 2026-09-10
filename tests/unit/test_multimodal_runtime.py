from __future__ import annotations

from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki import multimodal_runtime


class _FakeCuda:
    def __init__(self, available: bool) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def get_device_name(self, _: int) -> str:
        return "fake-gpu"


def _torch(*, version: str, cuda_version: str | None, available: bool) -> object:
    return SimpleNamespace(
        __version__=version,
        version=SimpleNamespace(cuda=cuda_version),
        cuda=_FakeCuda(available),
    )


def test_default_multimodal_runtime_is_torch_free(monkeypatch) -> None:
    monkeypatch.delenv("LLM_WIKI_MULTIMODAL_TORCH_BACKEND", raising=False)
    monkeypatch.setattr(
        multimodal_runtime.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(AssertionError("Torch must not be imported for the default profile")),
    )

    assert multimodal_runtime.validate_configured_multimodal_runtime(require_device=False) is None


def test_explicit_cuda_profile_requires_exact_torch_build(monkeypatch) -> None:
    monkeypatch.setattr(
        multimodal_runtime.importlib,
        "import_module",
        lambda _: _torch(version="2.8.0+cu128", cuda_version="12.8", available=False),
    )

    report = multimodal_runtime.validate_torch_runtime("cu128", require_device=False)

    assert report is not None
    assert report["torch_cuda_version"] == "12.8"
    with pytest.raises(RuntimeError, match="requires Torch CUDA 12.6"):
        multimodal_runtime.validate_torch_runtime("cu126", require_device=False)


def test_cpu_profile_rejects_a_cuda_build(monkeypatch) -> None:
    monkeypatch.setattr(
        multimodal_runtime.importlib,
        "import_module",
        lambda _: _torch(version="2.8.0+cu128", cuda_version="12.8", available=True),
    )

    with pytest.raises(RuntimeError, match="cpu multimodal profile"):
        multimodal_runtime.validate_torch_runtime("cpu", require_device=False)


def test_qwen3_vl_configuration_defaults_and_overrides() -> None:
    assert multimodal_runtime.configured_multimodal_backend({}) == "none"
    assert multimodal_runtime.configured_multimodal_model({}).endswith("Qwen3-VL-Embedding-2B")
    assert multimodal_runtime.configured_multimodal_dimension({}) == 1024
    environ = {
        "LLM_WIKI_MULTIMODAL_BACKEND": "transformers",
        "LLM_WIKI_MULTIMODAL_MODEL": "local/model",
        "LLM_WIKI_MULTIMODAL_DIMENSION": "1536",
    }
    assert multimodal_runtime.configured_multimodal_backend(environ) == "transformers"
    assert multimodal_runtime.configured_multimodal_model(environ) == "local/model"
    assert multimodal_runtime.configured_multimodal_dimension(environ) == 1536


@pytest.mark.parametrize("value", ["63", "2049", "not-an-int"])
def test_qwen3_vl_configuration_rejects_invalid_dimension(value: str) -> None:
    with pytest.raises(ValueError):
        multimodal_runtime.configured_multimodal_dimension(
            {"LLM_WIKI_MULTIMODAL_DIMENSION": value}
        )


def test_qwen3_vl_revision_is_optional() -> None:
    assert multimodal_runtime.configured_multimodal_revision({}) is None
    assert multimodal_runtime.configured_multimodal_revision(
        {"LLM_WIKI_MULTIMODAL_MODEL_REVISION": " rev-1 "}
    ) == "rev-1"


def test_remote_representation_requires_an_explicit_host_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_REPRESENTATION_SERVICE_URL", "http://representation:8790")
    monkeypatch.delenv("LLM_WIKI_REPRESENTATION_SERVICE_ALLOWED_HOSTS", raising=False)

    from kogwistar_llm_wiki.multimodal_projection import build_configured_multimodal_encoder

    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        build_configured_multimodal_encoder()
