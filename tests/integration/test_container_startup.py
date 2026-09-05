from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.e2e,
    pytest.mark.ci_full,
    pytest.mark.slow,
]


def _docker_e2e_enabled() -> bool:
    return os.getenv("KOGWISTAR_DOCKER_E2E", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _run(*args: str, timeout_seconds: float = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
        timeout=timeout_seconds,
    )


def test_built_container_enforces_startup_configuration():
    """Exercise the real image entrypoint; opt in because it builds the image."""
    if not _docker_e2e_enabled():
        pytest.skip("set KOGWISTAR_DOCKER_E2E=1 to run the Docker startup E2E test")
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")

    probe = _run("info")
    if probe.returncode != 0:
        pytest.fail(f"Docker daemon unavailable: {probe.stderr.strip()}")

    tag = f"kogwistar-llm-wiki-startup-e2e:{uuid.uuid4().hex[:12]}"
    try:
        build = _run("build", "--tag", tag, ".")
        assert build.returncode == 0, build.stdout + build.stderr

        invalid = _run(
            "run",
            "--rm",
            "--env",
            "KOGWISTAR_LLM_WIKI_EMBED_PROVIDER=ollama",
            "--env",
            "KOGWISTAR_LLM_WIKI_EMBED_MODEL=nomic-embed-text",
            "--env",
            "KOGWISTAR_LLM_WIKI_EMBED_DIMENSION=not-an-integer",
            tag,
            "python",
            "-c",
            "raise SystemExit('command should not execute')",
        )
        assert invalid.returncode == 78
        assert "configuration invalid" in invalid.stderr
        assert "invalid literal" in invalid.stderr or "dimension" in invalid.stderr
        assert "force-recreate" in invalid.stderr
        assert "command should not execute" not in invalid.stderr

        valid = _run(
            "run",
            "--rm",
            tag,
            "python",
            "-c",
            "print('startup command reached')",
        )
        assert valid.returncode == 0, valid.stdout + valid.stderr
        assert "startup command reached" in valid.stdout

        global_embedding = _run(
            "run",
            "--rm",
            "--env",
            "KOGWISTAR_LLM_WIKI_EMBED_PROVIDER=fake",
            "--env",
            "KOGWISTAR_LLM_WIKI_EMBED_MODEL=global-model",
            "--env",
            "KOGWISTAR_LLM_WIKI_EMBED_DIMENSION=6",
            tag,
            "python",
            "-c",
            "print('global embedding startup reached')",
        )
        assert global_embedding.returncode == 0, global_embedding.stdout + global_embedding.stderr
        assert "global embedding startup reached" in global_embedding.stdout
    finally:
        _run("image", "rm", "--force", tag)
