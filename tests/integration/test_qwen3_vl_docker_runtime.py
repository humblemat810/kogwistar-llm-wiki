"""Opt-in smoke coverage for the real Qwen3-VL Docker service."""

from __future__ import annotations

import base64
import json
from hashlib import sha256
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest


pytestmark = [pytest.mark.manual, pytest.mark.slow, pytest.mark.integration, pytest.mark.e2e]


def _enabled() -> bool:
    return os.getenv("KOGWISTAR_DOCKER_QWEN3_VL_E2E", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str, *, method: str = "GET", payload: dict[str, object] | None = None, token: str | None = None) -> dict[str, object]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def test_qwen3_vl_docker_gpu_service_returns_real_vectors() -> None:
    """Start the built CUDA image and exercise model-backed HTTP inference."""
    if not _enabled():
        pytest.skip("set KOGWISTAR_DOCKER_QWEN3_VL_E2E=1 for the real Docker model smoke test")

    image = os.getenv("LLM_WIKI_REPRESENTATION_IMAGE", "kogwistar-llm-wiki-representation:local")
    revision = os.getenv("LLM_WIKI_REPRESENTATION_MODEL_REVISION", "").strip()
    local_model_dir = os.getenv("LLM_WIKI_QWEN3_VL_MODEL_DIR", "").strip()
    if not revision:
        pytest.fail("LLM_WIKI_REPRESENTATION_MODEL_REVISION must contain the pinned model commit SHA")
    if subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    ).returncode != 0:
        pytest.fail(f"Docker image is missing; build {image} before running this test")
    if local_model_dir and not Path(local_model_dir).is_dir():
        pytest.fail(f"configured local Qwen3-VL model directory does not exist: {local_model_dir}")

    port = _free_port()
    token = os.getenv("LLM_WIKI_REPRESENTATION_TOKEN") or None
    cache_volume = os.getenv("LLM_WIKI_REPRESENTATION_HF_VOLUME", "llm-wiki-qwen3-vl-hf-cache")
    container = f"llm-wiki-qwen3-vl-e2e-{uuid.uuid4().hex[:10]}"
    command = [
        "docker", "run", "--detach", "--name", container,
        "--gpus", "all", "--publish", f"{port}:8790",
        "--volume", f"{cache_volume}:/var/lib/huggingface",
        "--env", "LLM_WIKI_REPRESENTATION_DEVICE=cuda",
        "--env", "LLM_WIKI_REPRESENTATION_TORCH_BACKEND=cu128",
        "--env", f"LLM_WIKI_REPRESENTATION_MODEL_REVISION={revision}",
        "--env", "LLM_WIKI_REPRESENTATION_DIMENSION=1024",
    ]
    if local_model_dir:
        command.extend([
            "--mount",
            f"type=bind,source={Path(local_model_dir).resolve()},target=/models/qwen3-vl,readonly",
            "--env",
            "LLM_WIKI_REPRESENTATION_MODEL=/models/qwen3-vl",
        ])
    if token:
        command.extend(["--env", f"LLM_WIKI_REPRESENTATION_TOKEN={token}"])
    command.append(image)
    timings: dict[str, float] = {}
    test_started = time.perf_counter()
    start_started = time.perf_counter()
    started = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", text=True)
    timings["docker_start_ms"] = round((time.perf_counter() - start_started) * 1000, 1)
    if started.returncode != 0:
        pytest.fail(started.stderr.strip() or started.stdout.strip())

    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + float(os.getenv("KOGWISTAR_DOCKER_QWEN3_VL_READY_TIMEOUT", "1800"))
        last_error = "service did not become ready"
        readiness_started = time.perf_counter()
        while time.monotonic() < deadline:
            try:
                _request(f"{base_url}/readyz")
                timings["ready_ms"] = round((time.perf_counter() - readiness_started) * 1000, 1)
                break
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last_error = str(exc)
                state = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Status}}", container],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    text=True,
                )
                if state.returncode == 0 and state.stdout.strip() in {"exited", "dead"}:
                    logs = subprocess.run(
                        ["docker", "logs", container],
                        capture_output=True,
                        encoding="utf-8",
                        errors="replace",
                        text=True,
                    )
                    pytest.fail(f"Qwen3-VL Docker service exited during startup: {last_error}\n{logs.stdout}\n{logs.stderr}")
                time.sleep(2)
        else:
            logs = subprocess.run(
                ["docker", "logs", container],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                text=True,
            )
            pytest.fail(f"Qwen3-VL Docker service did not become ready: {last_error}\n{logs.stdout}\n{logs.stderr}")

        capabilities_started = time.perf_counter()
        capabilities = _request(f"{base_url}/v1/capabilities", token=token)
        timings["capabilities_ms"] = round((time.perf_counter() - capabilities_started) * 1000, 1)
        profile = capabilities["profile"]
        assert isinstance(profile, dict)
        assert profile["model"] in {"Qwen/Qwen3-VL-Embedding-2B", "/models/qwen3-vl"}
        assert profile["dimension"] == 1024

        # A tiny deterministic PNG keeps this test provider/input independent.
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        png = base64.b64encode(png_bytes).decode("ascii")
        inference_started = time.perf_counter()
        response = _request(
            f"{base_url}/v1/represent",
            method="POST",
            token=token,
            payload={
                "contract_version": "v1",
                "request_id": "docker-qwen3-vl-smoke",
                "operation": "document",
                "profile_fingerprint": profile["fingerprint"],
                "items": [
                    {"item_id": "text-1", "modality": "text", "text": "A small smoke-test passage."},
                    {"item_id": "image-1", "modality": "image", "asset": {"data_base64": png, "content_type": "image/png", "sha256": sha256(png_bytes).hexdigest()}},
                ],
            },
        )
        timings["represent_ms"] = round((time.perf_counter() - inference_started) * 1000, 1)
        assert response["results"]
    finally:
        cleanup_started = time.perf_counter()
        subprocess.run(
            ["docker", "stop", "--time", "10", container],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
        )
        subprocess.run(
            ["docker", "rm", "--force", container],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
        )
        timings["cleanup_ms"] = round((time.perf_counter() - cleanup_started) * 1000, 1)
        timings["total_ms"] = round((time.perf_counter() - test_started) * 1000, 1)
        print(f"qwen3_vl_docker_timings={json.dumps(timings, sort_keys=True)}")
