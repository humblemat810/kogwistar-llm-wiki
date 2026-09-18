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
    assert "app-data-init:" in text
    assert "service_completed_successfully" in text


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
    assert "LLM_WIKI_POSTGRES_MEMORY_LIMIT" in gpu
    assert "LLM_WIKI_APP_CPU_LIMIT" in gpu
    assert "LLM_WIKI_EMBEDDING_MEMORY_LIMIT" in gpu


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


def test_combined_overlay_uses_one_process_and_suppresses_split_roles() -> None:
    text = (Path(__file__).parents[2] / "compose.combined.yml").read_text(encoding="utf-8")
    assert "serve" in text
    assert "--mcp-port" in text
    assert "profiles: [split]" in text
    assert "LLM_WIKI_COMBINED_MEMORY_LIMIT" in text
    assert "LLM_WIKI_COMBINED_CPU_LIMIT" in text


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
    assert "mem_limit:" in text
    assert "cpus:" in text


def test_codex_overlay_is_opt_in_and_persists_auth_without_host_mounts() -> None:
    text = (Path(__file__).parents[2] / "compose.codex.yml").read_text(encoding="utf-8")
    assert "STANDALONE OPT-IN CODEX CONTAINER" in text
    assert "codex_auth:/var/lib/codex" in text
    assert "maintenance:" not in text
    assert "host.docker.internal" not in text
    assert "docker.sock" not in text
    assert "--entrypoint codex codex login --device-auth" in text
    assert "LLM_WIKI_CODEX_MEMORY_LIMIT" in text

    memory_text = (Path(__file__).parents[2] / "compose.codex-memory.yml").read_text(encoding="utf-8")
    assert "KOGWISTAR_MAINTENANCE_CODEX_BASE_URL: http://codex:8791" in memory_text
    assert "condition: service_healthy" in memory_text


def test_codex_dockerfile_overlays_current_bridge_source() -> None:
    text = (Path(__file__).parents[2] / "Dockerfile.codex").read_text(encoding="utf-8")
    assert "COPY docker/codex_bridge.mjs" in text
    assert "npm install --global @openai/codex" in text


def test_codex_container_bridge_skips_git_trust_check_in_isolated_runtime() -> None:
    text = (Path(__file__).parents[2] / "docker" / "codex_bridge.mjs").read_text(encoding="utf-8")
    assert "--skip-git-repo-check" in text
    assert '"--sandbox", "read-only"' in text
    assert '"approval_policy=never"' in text


def test_codex_ca_overlay_requires_explicit_read_only_bundle() -> None:
    text = (Path(__file__).parents[2] / "compose.codex-ca.yml").read_text(encoding="utf-8")
    assert "LLM_WIKI_CODEX_CA_BUNDLE_FILE:?" in text
    assert ":/run/codex-ca/ca-bundle.pem:ro" in text
    assert "SSL_CERT_FILE: /run/codex-ca/ca-bundle.pem" in text
    assert "verify" not in text.lower() or "verification" in text.lower()


def test_codex_launchers_select_standalone_or_memory_file_sets() -> None:
    root = Path(__file__).parents[2]
    powershell = (root / "scripts" / "start_codex_compose.ps1").read_text(encoding="utf-8")
    shell = (root / "scripts" / "start_codex_compose.sh").read_text(encoding="utf-8")
    for text in (powershell, shell):
        assert "compose.codex.yml" in text
        assert "compose.codex-ca.yml" in text
        assert "compose.codex-memory.yml" in text
        assert "standalone" in text
        assert "memory" in text
    assert "--entrypoint codex codex login --device-auth" in powershell
    assert "--entrypoint codex codex login --device-auth" in shell
    assert "if (-not $?)" in powershell
    assert "Set-Location $repo" in powershell
    assert "script_dir=" in shell
    assert "--login" in shell


def test_codex_tui_exposes_host_bridge_as_a_distinct_mode(tmp_path: Path) -> None:
    from kogwistar_llm_wiki.codex.codex_compose_tui import (
        TuiConfiguration,
        _host_environment,
        _repository_root,
        build_plan,
        merge_configuration_env,
    )

    host_steps = build_plan("host", build=False, login=False, project="ignored")
    assert host_steps[0].background is True
    assert host_steps[0].command[1:] == ["-m", "kogwistar_llm_wiki", "codex-bridge", "--host", "0.0.0.0", "--port", "8791"]
    memory_steps = build_plan("host-memory", build=False, login=True, project="demo")
    assert memory_steps[0].command == ["codex", "login"]
    assert "compose.memory-agent.yml" in memory_steps[-1].command
    assert _host_environment()["KOGWISTAR_MAINTENANCE_PROVIDER"] == "codex"
    assert _host_environment()["KOGWISTAR_MAINTENANCE_CODEX_BASE_URL"] == "http://host.docker.internal:8791"
    assert (_repository_root() / "compose.codex.yml").exists()

    configuration = TuiConfiguration(
        stack="combined",
        embedding="vllm",
        maintenance_enabled=True,
        request_enabled=False,
        background_enabled=True,
        embedding_crop_token_budget=7000,
        profile_ladder_json='[{"name":"primary","provider":"codex","profile":"high"}]',
    )
    configured = build_plan("host-memory", build=False, login=False, project="demo", configuration=configuration)
    assert "compose.combined.yml" in configured[-1].command
    assert "compose.embedding-vllm.yml" in configured[-1].command
    assert configured[-1].environment["LLM_WIKI_MAINTENANCE_REQUEST_ENABLED"] == "false"

    env_file = tmp_path / "tui-config.env"
    env_file.write_text("POSTGRES_PASSWORD=keep-me\nLLM_WIKI_COMBINED_CPU_LIMIT=old\n", encoding="utf-8")
    merge_configuration_env(env_file, configuration)
    text = env_file.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=keep-me" in text
    assert "LLM_WIKI_COMBINED_CPU_LIMIT=0.25" in text
    assert "LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED=true" in text
    assert 'LLM_WIKI_MAINTENANCE_PROFILE_LADDER=[{"name":"primary","provider":"codex","profile":"high"}]' in text
    assert "--profile-ladder-file" in (Path(__file__).parents[2] / "src" / "kogwistar_llm_wiki" / "codex" / "codex_compose_tui.py").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="between 1 and max model length"):
        TuiConfiguration(embedding_max_model_len=8192, embedding_crop_token_budget=8193)


def test_lxc_helper_is_linux_only_and_non_mutating_by_default() -> None:
    script = Path("scripts/setup_lxc_docker.sh").read_text(encoding="utf-8")
    assert "--check" in script
    assert "--apply" in script
    assert "--print-config" in script
    assert "apt-get install -y docker-ce" in script
    assert "does not create or reconfigure an LXC" in script


def test_generated_stack_exposes_tunable_resource_limits() -> None:
    text = render_compose(ComposeOptions(model_revision="abc123", with_otel=True, with_oauth=True))
    for name in (
        "LLM_WIKI_POSTGRES_MEMORY_LIMIT",
        "LLM_WIKI_APP_MEMORY_LIMIT",
        "LLM_WIKI_EMBEDDING_MEMORY_LIMIT",
        "LLM_WIKI_GRAFANA_MEMORY_LIMIT",
        "LLM_WIKI_OAUTH_MEMORY_LIMIT",
    ):
        assert name in text
