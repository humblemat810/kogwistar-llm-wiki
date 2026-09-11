from __future__ import annotations

from pathlib import Path

import pytest

from kogwistar_llm_wiki.compose_config import (
    ComposeConfigurationError,
    ComposeOptions,
    check_compose_text,
    render_compose,
    write_compose,
)


def test_gpu_bundle_is_complete_without_secrets() -> None:
    text = render_compose(ComposeOptions(model_revision="abc123", with_otel=True, with_oauth=True))
    result = check_compose_text(text)
    assert result["valid"] is True
    assert "driver: nvidia" in text
    assert "grafana/otel-lgtm" in text
    assert "mock-oauth2-server" in text
    assert "${POSTGRES_PASSWORD" in text
    assert "change-this-development-password" not in text


def test_gpu_auto_selects_vllm_and_cpu_keeps_reference_service() -> None:
    gpu = render_compose(ComposeOptions(model_revision="abc123"))
    cpu = render_compose(ComposeOptions(mode="cpu", model_revision="abc123"))
    assert "LLM_WIKI_MULTIMODAL_BACKEND: \"${LLM_WIKI_MULTIMODAL_BACKEND:-vllm}\"" in gpu
    assert "LLM_WIKI_EMBEDDING_VLLM_URL: \"${LLM_WIKI_EMBEDDING_VLLM_URL:-http://embedding:8000}\"" in gpu
    assert "vllm/vllm-openai@sha256" in gpu
    assert "Dockerfile.embedding-service" not in gpu
    assert "LLM_WIKI_MULTIMODAL_BACKEND: \"${LLM_WIKI_MULTIMODAL_BACKEND:-transformers}\"" in cpu
    assert "Dockerfile.embedding-service" in cpu
    assert "--max-model-len" in gpu
    assert "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET" in gpu
    assert "--enforce-eager" in gpu


def test_compose_context_knobs_reject_invalid_budget() -> None:
    with pytest.raises(ComposeConfigurationError, match="cannot exceed"):
        render_compose(
            ComposeOptions(
                model_revision="abc123",
                embedding_max_model_len=2048,
                embedding_crop_token_budget=7680,
            )
        )
    with pytest.raises(ComposeConfigurationError, match="cannot exceed 8192"):
        render_compose(ComposeOptions(model_revision="abc123", embedding_max_model_len=8193))


def test_vllm_is_explicitly_gpu_only() -> None:
    with pytest.raises(ComposeConfigurationError, match="GPU-only"):
        render_compose(ComposeOptions(mode="cpu", embedding_backend="vllm", model_revision="abc123"))


def test_cpu_and_text_only_modes_have_expected_services() -> None:
    cpu = render_compose(ComposeOptions(mode="cpu", model_revision="abc123"))
    text_only = render_compose(ComposeOptions(mode="text-only"))
    assert "LLM_WIKI_EMBEDDING_DEVICE: cpu" in cpu
    assert "driver: nvidia" not in cpu
    assert "  embedding:" not in text_only
    with pytest.raises(ComposeConfigurationError, match="embedded Chroma"):
        render_compose(ComposeOptions(backend="chroma", mode="text-only"))


def test_chroma_compose_is_rejected_even_for_full_qwen_dimension() -> None:
    with pytest.raises(ComposeConfigurationError, match="embedded Chroma"):
        render_compose(
            ComposeOptions(backend="chroma", mode="gpu", model_revision="abc123", embedding_dimension=2048)
        )


def test_disabled_auth_is_explicitly_disabled() -> None:
    text = render_compose(ComposeOptions(mode="text-only"))
    assert 'LLM_WIKI_AUTH_MODE: "disabled"' in text


@pytest.mark.parametrize(
    "options, message",
    [
        (ComposeOptions(), "model_revision is required"),
        (ComposeOptions(mode="gpu", model_revision="abc123", embedding_dimension=2048), "above 1536"),
        (ComposeOptions(mode="invalid", model_revision="abc123"), "mode must be"),
    ],
)
def test_unsafe_compose_options_fail_closed(options: ComposeOptions, message: str) -> None:
    with pytest.raises(ComposeConfigurationError, match=message):
        render_compose(options)


def test_check_reports_missing_stack_elements() -> None:
    result = check_compose_text("services:\n  web:\n    image: example\n")
    assert result["valid"] is False
    assert any("llm-wiki" in error for error in result["errors"])


def test_write_compose_is_utf8_and_returns_path(tmp_path: Path) -> None:
    destination = write_compose(tmp_path / "generated.yml", ComposeOptions(model_revision="abc123"))
    assert destination.exists()
    assert "services:" in destination.read_text(encoding="utf-8")


def test_memory_agent_combination_enables_observability_and_auth() -> None:
    text = render_compose(ComposeOptions(mode="text-only", with_otel=True, with_oauth=True, auth_mode="static_token"))
    assert "grafana:" in text
    assert 'LLM_WIKI_OTEL_ENABLED: "${LLM_WIKI_OTEL_ENABLED:-true}"' in text
    assert "oauth:" in text
    assert 'LLM_WIKI_AUTH_MODE: "static_token"' in text
    assert "postgres:" in text


def test_static_compose_helper_contains_persistent_service_contract() -> None:
    text = (Path(__file__).parents[2] / "frontend" / "public" / "compose.html").read_text(
        encoding="utf-8"
    )
    assert "KOGWISTAR_POSTGRES_DSN" in text
    assert "app_data:/var/lib/llm-wiki" in text
    assert "host.docker.internal:host-gateway" in text
    assert "embedding_hf_cache:/var/lib/huggingface" in text
    assert "LLM_WIKI_OTEL_ENABLED" in text
    assert "condition: service_healthy" in text
    assert "Embedded Chroma is single-process only" in text


def test_vllm_overlay_is_gpu_only_and_requires_pinned_identity() -> None:
    text = (Path(__file__).parents[2] / "compose.embedding-vllm.yml").read_text(
        encoding="utf-8"
    )
    assert "EXPERIMENTAL GPU OVERLAY ONLY" in text
    assert "vllm/vllm-openai@sha256" in text
    assert "LLM_WIKI_EMBEDDING_VLLM_IMAGE:?" in text
    assert "LLM_WIKI_MULTIMODAL_MODEL_REVISION:?" in text
    assert "--runner" in text and "pooling" in text
    assert "driver: nvidia" in text
    assert "expose:" in text
    assert "ports:" not in text
