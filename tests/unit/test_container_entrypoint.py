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
    monkeypatch.setattr(container_entrypoint, "_resolve_embedding_functions", lambda: {})
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


def test_container_contract_keeps_entrypoint_and_compose_commands():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yml").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["python", "-m", "kogwistar_llm_wiki.container_entrypoint"]' in dockerfile
    assert "      - llm-wiki\n" in compose
    assert "      - workbench\n" in compose
    assert "      - mcp\n" in compose


def test_dockerfile_pins_and_build_checks_fastmcp_imports():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    parser_pyproject = (ROOT / "kg-doc-parser" / "pyproject.toml").read_text(encoding="utf-8")
    assert '"fastmcp==3.0.0"' in dockerfile
    assert 'fastmcp = "3.0.0"' in parser_pyproject
    assert "from fastmcp import FastMCP" in dockerfile
    assert "from fastmcp.server.auth import StaticTokenVerifier, require_scopes" in dockerfile
