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


def test_vllm_backend_reads_explicit_remote_settings() -> None:
    environ = {
        "LLM_WIKI_MULTIMODAL_BACKEND": "vllm",
        "LLM_WIKI_EMBEDDING_VLLM_URL": "http://embedding:8000",
        "LLM_WIKI_EMBEDDING_VLLM_TOKEN": "secret",
        "LLM_WIKI_EMBEDDING_VLLM_IMAGE": "vllm/vllm-openai@sha256:" + "a" * 64,
        "LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS": "embedding",
    }

    assert multimodal_runtime.configured_multimodal_backend(environ) == "vllm"
    assert multimodal_runtime.configured_vllm_url(environ) == "http://embedding:8000"
    assert multimodal_runtime.configured_vllm_token(environ) == "secret"
    assert multimodal_runtime.configured_vllm_allowed_hosts(environ) == ("embedding",)
    assert multimodal_runtime.configured_vllm_image(environ).endswith("a" * 64)


def test_embedding_context_knobs_validate_and_default() -> None:
    assert multimodal_runtime.configured_embedding_max_model_len({}) == 8192
    assert multimodal_runtime.configured_embedding_crop_token_budget({}) == 7680
    values = {
        "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN": "8192",
        "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET": "7680",
    }
    assert multimodal_runtime.configured_embedding_max_model_len(values) == 8192
    assert multimodal_runtime.configured_embedding_crop_token_budget(values) == 7680
    with pytest.raises(ValueError, match="cannot exceed"):
        multimodal_runtime.configured_embedding_crop_token_budget(
            {"LLM_WIKI_EMBEDDING_MAX_MODEL_LEN": "2048", "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET": "7680"}
        )
    with pytest.raises(ValueError, match="cannot exceed 8192"):
        multimodal_runtime.configured_embedding_max_model_len(
            {"LLM_WIKI_EMBEDDING_MAX_MODEL_LEN": "8193"}
        )


def test_vllm_builder_is_remote_only(monkeypatch) -> None:
    values = {
        "LLM_WIKI_MULTIMODAL_BACKEND": "vllm",
        "LLM_WIKI_EMBEDDING_VLLM_URL": "http://embedding:8000",
        "LLM_WIKI_EMBEDDING_VLLM_TOKEN": "secret",
        "LLM_WIKI_EMBEDDING_VLLM_IMAGE": "vllm/vllm-openai@sha256:" + "a" * 64,
        "LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS": "embedding",
        "LLM_WIKI_MULTIMODAL_MODEL_REVISION": "revision",
        "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN": "8192",
        "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET": "7680",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("LLM_WIKI_EMBEDDING_SERVICE_URL", raising=False)

    from kogwistar_llm_wiki.multimodal_projection import build_configured_multimodal_encoder
    from kogwistar_llm_wiki.vllm_remote import VllmMultimodalEncoder

    encoder = build_configured_multimodal_encoder()
    assert isinstance(encoder, VllmMultimodalEncoder)
    assert encoder.profile.provider == "vllm"


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


def test_embedding_service_uses_canonical_environment_names() -> None:
    environ = {
        "LLM_WIKI_EMBEDDING_SERVICE_URL": "http://embedding:8790",
        "LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS": "embedding",
    }

    assert multimodal_runtime.configured_embedding_service_url(environ) == "http://embedding:8790"
    assert multimodal_runtime.configured_embedding_service_allowed_hosts(environ) == ("embedding",)


def test_remote_embedding_requires_an_explicit_host_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_EMBEDDING_SERVICE_URL", "http://embedding:8790")
    monkeypatch.delenv("LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS", raising=False)

    from kogwistar_llm_wiki.multimodal_projection import build_configured_multimodal_encoder

    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        build_configured_multimodal_encoder()
