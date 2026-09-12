from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from kogwistar_llm_wiki import container_entrypoint

ROOT = Path(__file__).resolve().parents[2]


def test_container_entrypoint_rejects_invalid_configuration(monkeypatch, capsys):
    def fail_validation():
        raise ValueError("knowledge embedding dimension is missing")

    monkeypatch.setattr(container_entrypoint, "_resolve_embedding_functions", fail_validation)
    exec_calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    monkeypatch.setattr(
        container_entrypoint.os,
        "execvpe",
        lambda executable, args, env: exec_calls.append((tuple(args), env)),
    )

    result = container_entrypoint.main(["llm-wiki", "workbench"])

    assert result == 78
    assert exec_calls == []
    error = capsys.readouterr().err
    assert "configuration invalid" in error
    assert "docker compose up -d --force-recreate" in error


def test_container_entrypoint_executes_requested_command_after_validation(monkeypatch):
    monkeypatch.setattr(container_entrypoint, "_resolve_embedding_functions", dict)
    monkeypatch.setattr(container_entrypoint, "validate_configured_multimodal_runtime", lambda **_: None)
    captured = SimpleNamespace(executable=None, args=None, env=None)

    def fake_exec(executable, args, env):
        captured.executable = executable
        captured.args = list(args)
        captured.env = env

    monkeypatch.setattr(container_entrypoint.os, "execvpe", fake_exec)

    result = container_entrypoint.main(["llm-wiki", "mcp", "--port", "8780"])

    assert result == 0
    assert captured.executable == "llm-wiki"
    assert captured.args == ["llm-wiki", "mcp", "--port", "8780"]
    assert captured.env is container_entrypoint.os.environ


def test_container_entrypoint_rejects_missing_command(capsys):
    assert container_entrypoint.main([]) == 64
    assert "no command configured" in capsys.readouterr().err


def test_real_module_entrypoint_reports_invalid_environment_before_exec():
    environment = os.environ.copy()
    environment.update(
        {
            "KOGWISTAR_LLM_WIKI_EMBED_PROVIDER": "ollama",
            "KOGWISTAR_LLM_WIKI_EMBED_MODEL": "nomic-embed-text",
            "KOGWISTAR_LLM_WIKI_EMBED_DIMENSION": "not-an-integer",
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "kogwistar_llm_wiki.container_entrypoint", "python", "-c", "pass"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 78
    assert "configuration invalid" in result.stderr
    assert "--force-recreate" in result.stderr


def test_global_embedding_environment_passes_startup_validation(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_PROVIDER", "fake")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_MODEL", "global-model")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_DIMENSION", "6")

    container_entrypoint._resolve_embedding_functions()


def test_container_entrypoint_rejects_selected_multimodal_profile_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(container_entrypoint, "_resolve_embedding_functions", dict)
    monkeypatch.setenv("LLM_WIKI_MULTIMODAL_BACKEND", "transformers")

    assert container_entrypoint.main(["llm-wiki", "workbench"]) == 78
    error = capsys.readouterr().err
    assert "not supported in the production app container" in error
    assert "LLM_WIKI_EMBEDDING_SERVICE_URL" in error


def test_container_entrypoint_validates_vllm_before_exec(monkeypatch, capsys):
    monkeypatch.setattr(container_entrypoint, "_resolve_embedding_functions", dict)
    monkeypatch.setattr(container_entrypoint, "build_configured_multimodal_encoder", lambda: object())
    monkeypatch.setenv("LLM_WIKI_MULTIMODAL_BACKEND", "vllm")
    monkeypatch.setenv("LLM_WIKI_EMBEDDING_VLLM_URL", "http://embedding:8000")
    monkeypatch.delenv("LLM_WIKI_EMBEDDING_SERVICE_URL", raising=False)

    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        container_entrypoint.os,
        "execvpe",
        lambda executable, args, env: calls.append((executable, list(args))),
    )

    assert container_entrypoint.main(["llm-wiki", "workbench"]) == 0
    assert calls == [("llm-wiki", ["llm-wiki", "workbench"])]


def test_container_entrypoint_rejects_incomplete_vllm_configuration(monkeypatch, capsys):
    monkeypatch.setattr(container_entrypoint, "_resolve_embedding_functions", dict)
    monkeypatch.setenv("LLM_WIKI_MULTIMODAL_BACKEND", "vllm")
    monkeypatch.setenv("LLM_WIKI_EMBEDDING_VLLM_URL", "http://embedding:8000")
    monkeypatch.delenv("LLM_WIKI_EMBEDDING_SERVICE_URL", raising=False)

    assert container_entrypoint.main(["llm-wiki", "workbench"]) == 78
    assert "vLLM requires" in capsys.readouterr().err


def test_container_contract_keeps_entrypoint_and_compose_commands():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yml").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["python", "-m", "kogwistar_llm_wiki.container_entrypoint"]' in dockerfile
    assert "      - llm-wiki\n" in compose
    assert "      - workbench\n" in compose
    assert "      - mcp\n" in compose


def test_dockerfile_pins_and_build_checks_fastmcp_imports():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    constraints = (ROOT / "docker" / "container-constraints.txt").read_text(encoding="utf-8")
    parser_pyproject = (ROOT / "kg-doc-parser" / "pyproject.toml").read_text(encoding="utf-8")
    assert "COPY docker/container-constraints.txt" in dockerfile
    assert "fastmcp==3.0.0" in constraints
    assert 'fastmcp = "3.0.0"' in parser_pyproject
    assert "from fastmcp import FastMCP" in dockerfile
    assert "from fastmcp.server.auth import StaticTokenVerifier, require_scopes" in dockerfile


def test_application_dockerfile_separates_churn_layers_and_runtime_tools() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM ${PYTHON_IMAGE} AS runtime", 1)[1]

    assert "# syntax=docker/dockerfile:1.7" in dockerfile
    assert "--mount=type=cache,target=/root/.cargo/registry" in dockerfile
    assert "--mount=type=cache,target=/app/kogwistar/rust/target" in dockerfile
    assert dockerfile.index("COPY kogwistar ./kogwistar") < dockerfile.index("COPY kg-doc-parser ./kg-doc-parser")
    assert dockerfile.index("COPY kg-doc-parser ./kg-doc-parser") < dockerfile.index("COPY src ./src")
    assert "COPY --from=app-builder --chown=10001:10001 /opt/venv /opt/venv" in dockerfile
    assert "apt-get install" not in runtime
    assert "rustup.sh" not in runtime
    assert "RUN python -m pip uninstall -y maturin poetry-core wheel setuptools" in dockerfile
    assert "KG_DOC_PARSER_JOBLIB_CACHE_DIR=/var/lib/llm-wiki/parser-cache" in dockerfile
    assert "GKE_JOBLIB_CACHE_DIR=/var/lib/llm-wiki/kogwistar-cache" in dockerfile
    assert "mkdir -p /app/.cache /app/.version_chain /app/.llm_cache /app/.kg_extract" in runtime
    assert "chown 10001:10001 /app/.cache /app/.version_chain /app/.llm_cache" in runtime
    assert "/app/.kg_extract /app/application_logs.db /var/lib/llm-wiki/logs" in runtime
    assert "COPY kogwistar" not in runtime
    assert "COPY kg-doc-parser" not in runtime


def test_docker_multimodal_contract_is_explicit_and_opt_in() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    embedding_dockerfile = (ROOT / "Dockerfile.embedding-service").read_text(encoding="utf-8")
    compose_override = (ROOT / "compose.multimodal.yml").read_text(encoding="utf-8")

    assert "LLM_WIKI_MULTIMODAL_TORCH_BACKEND" not in dockerfile
    assert "Dockerfile.embedding-service" in compose_override
    assert "requirements/multimodal/torch-${LLM_WIKI_EMBEDDING_TORCH_BACKEND}.txt" in embedding_dockerfile
    assert "fastapi" in embedding_dockerfile
    assert "LLM_WIKI_EMBEDDING_MODEL" in compose_override
    assert "LLM_WIKI_EMBEDDING_MODEL_REVISION" in compose_override
    assert 'install_multimodal_runtime.py' not in dockerfile
    cuda_override = (ROOT / "compose.embedding-cuda.yml").read_text(encoding="utf-8")
    assert "driver: nvidia" in cuda_override
    assert "capabilities: [gpu]" in cuda_override


def test_embedding_image_isolated_from_application_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile.embedding-service").read_text(encoding="utf-8")
    assert "embedding-contract/pyproject.toml" in dockerfile
    assert "embedding-service/pyproject.toml" in dockerfile
    for forbidden in ("kogwistar", "kg-doc-parser", "obsidian", "maturin", "cargo", "gcc"):
        assert forbidden not in dockerfile.lower()
    assert "llm_wiki_embedding_contract" in dockerfile
    assert "llm_wiki_embedding_service" in dockerfile


def test_embedding_dockerfile_keeps_runtime_dependencies_before_service_source() -> None:
    dockerfile = (ROOT / "Dockerfile.embedding-service").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM ${PYTHON_IMAGE} AS runtime", 1)[1]

    assert "# syntax=docker/dockerfile:1.7" in dockerfile
    assert dockerfile.index("COPY requirements/multimodal") < dockerfile.index(
        "COPY src/llm_wiki_embedding_service"
    )
    assert dockerfile.index('python -m pip install -r "requirements/multimodal/torch-${LLM_WIKI_EMBEDDING_TORCH_BACKEND}.txt"') < dockerfile.index(
        "COPY src/llm_wiki_embedding_service"
    )
    assert "COPY --from=embedding-builder --chown=10001:10001 /opt/venv /opt/venv" in dockerfile
    assert "apt-get install" not in runtime
    assert "COPY src/llm_wiki_embedding_service" not in runtime
