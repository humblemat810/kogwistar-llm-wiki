"""Guided terminal launcher for Codex and the LLM-Wiki memory stack."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LaunchMode = Literal["standalone", "memory", "host", "host-memory"]


@dataclass(frozen=True)
class LaunchStep:
    """One visible process action in a guided launch plan."""

    description: str
    command: list[str]
    background: bool = False


def _repository_root() -> Path:
    markers = ("compose.codex.yml", "scripts")
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if all((candidate / marker).exists() for marker in markers):
            return candidate
    installed_root = Path(__file__).resolve().parents[2]
    if all((installed_root / marker).exists() for marker in markers):
        return installed_root
    raise RuntimeError("run codex-compose from an LLM-Wiki checkout containing compose.codex.yml and scripts")


def _choose_mode() -> LaunchMode:
    print("Codex setup")
    print("1. Standalone Codex container")
    print("2. Full LLM-Wiki memory stack with Codex container")
    print("3. Host Codex bridge only")
    print("4. Full LLM-Wiki memory stack with host bridge")
    while True:
        answer = input("Choose [1/2/3/4] (default 1): ").strip() or "1"
        choices: dict[str, LaunchMode] = {
            "1": "standalone",
            "2": "memory",
            "3": "host",
            "4": "host-memory",
        }
        if answer in choices:
            return choices[answer]
        print("Please choose 1, 2, 3, or 4.")


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{suffix}]: ").strip().lower()
    return default if not answer else answer in {"y", "yes"}


def _compose_launcher(mode: Literal["standalone", "memory"], *, build: bool, login: bool, project: str) -> list[str]:
    root = _repository_root()
    if os.name == "nt":
        command = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "start_codex_compose.ps1"),
            "-Mode",
            mode,
            "-Project",
            project,
        ]
        if build:
            command.append("-Build")
        if login:
            command.append("-Login")
        return command
    command = ["bash", str(root / "scripts" / "start_codex_compose.sh"), "--mode", mode, "--project", project]
    if build:
        command.append("--build")
    if login:
        command.append("--login")
    return command


def _host_bridge_command() -> list[str]:
    return [sys.executable, "-m", "kogwistar_llm_wiki", "codex-bridge", "--host", "0.0.0.0", "--port", "8791"]


def _host_memory_compose_command(project: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        "compose.yml",
        "-f",
        "compose.memory-agent.yml",
        "up",
        "-d",
    ]


def build_plan(mode: LaunchMode, *, build: bool, login: bool, project: str) -> list[LaunchStep]:
    """Build the exact visible process steps for a selected deployment mode."""
    if mode in {"standalone", "memory"}:
        return [
            LaunchStep(
                "start the CA-aware Codex Compose launcher",
                _compose_launcher(mode, build=build, login=login, project=project),
            )
        ]
    steps: list[LaunchStep] = []
    if login:
        steps.append(LaunchStep("authenticate the host Codex CLI with its normal SSO flow", ["codex", "login"]))
    steps.append(LaunchStep("start the host Codex bridge", _host_bridge_command(), background=True))
    if mode == "host-memory":
        steps.append(LaunchStep("start the memory stack using the host bridge", _host_memory_compose_command(project)))
    return steps


def _format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


def _host_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["KOGWISTAR_MAINTENANCE_PROVIDER"] = "codex"
    environment["KOGWISTAR_MAINTENANCE_PROVIDER_CHAIN"] = "codex,ollama"
    environment["KOGWISTAR_MAINTENANCE_CODEX_BASE_URL"] = "http://host.docker.internal:8791"
    environment["KOGWISTAR_MAINTENANCE_CODEX_API_KEY_ENV"] = "LLM_WIKI_CODEX_BRIDGE_TOKEN"
    return environment


def _check_host_bridge_configuration() -> None:
    if not os.environ.get("LLM_WIKI_CODEX_BRIDGE_TOKEN"):
        raise RuntimeError("set LLM_WIKI_CODEX_BRIDGE_TOKEN before starting the host bridge")


def _wait_for_host_bridge(process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"host bridge exited before becoming healthy (exit code {process.returncode})")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8791/healthz", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("host bridge did not become healthy on http://127.0.0.1:8791")


def _host_bridge_is_healthy() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8791/healthz", timeout=0.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _execute_plan(mode: LaunchMode, steps: list[LaunchStep]) -> int:
    if mode in {"host", "host-memory"}:
        _check_host_bridge_configuration()
    processes: list[subprocess.Popen] = []
    for step in steps:
        if step.background:
            if _host_bridge_is_healthy():
                print("Host bridge is already healthy; reusing it.")
                continue
            process = subprocess.Popen(step.command, cwd=_repository_root(), env=_host_environment())
            processes.append(process)
            try:
                _wait_for_host_bridge(process)
            except RuntimeError:
                process.terminate()
                raise
            print(f"Host bridge started with process id {process.pid}.")
            continue
        result = subprocess.run(
            step.command,
            cwd=_repository_root(),
            env=_host_environment() if mode == "host-memory" else None,
            check=False,
        )
        if result.returncode:
            for process in processes:
                process.terminate()
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["standalone", "memory", "host", "host-memory"])
    parser.add_argument("--project", default="llm-wiki-memory")
    parser.add_argument("--build", action="store_true", help="Build the Codex image before starting")
    parser.add_argument("--login", action="store_true", help="Run authentication before starting")
    parser.add_argument("--execute", action="store_true", help="Execute after showing the selected plan")
    parser.add_argument("--dry-run", action="store_true", help="Show the selected plan without running it")
    args = parser.parse_args(argv)

    mode = args.mode or _choose_mode()
    container_mode = mode in {"standalone", "memory"}
    build = args.build or (not args.mode and container_mode and _ask_yes_no("Build the Codex image first?"))
    login = args.login or (not args.mode and _ask_yes_no("Run authentication first?"))
    if not container_mode and build:
        parser.error("--build applies only to container modes")
    try:
        steps = build_plan(mode, build=build, login=login, project=args.project)
    except RuntimeError as error:
        print(f"Setup required: {error}", file=sys.stderr)
        return 1

    labels = {
        "standalone": "standalone Codex container",
        "memory": "LLM-Wiki memory stack with Codex container",
        "host": "host Codex bridge",
        "host-memory": "LLM-Wiki memory stack with host Codex bridge",
    }
    print()
    print("Selected:", labels[mode])
    if container_mode:
        print("Reasoning: Codex uses the signed-in Codex/GPT service; Ollama is only an optional fallback")
        print("Embeddings: remain a separate vLLM or reference embedding service")
        print("CA: the launcher prepares host trusted roots and mounts them read-only")
        print("Credentials: stored in the named codex_auth volume, never in the image")
        print("Login: container device-code authentication is separate from host SSO")
        compose_files = "compose.codex.yml + compose.codex-ca.yml"
        if mode == "memory":
            compose_files = "compose.yml + compose.memory-agent.yml + compose.codex.yml + compose.codex-memory.yml + compose.codex-ca.yml"
        print(f"Compose files: {compose_files}")
        if login:
            print("Device login: docker compose ... run --rm -it --entrypoint codex codex login --device-auth")
    else:
        print("Reasoning: Codex uses the host signed-in Codex/GPT service; Ollama is only an optional fallback")
        print("Embeddings: remain a separate vLLM or reference embedding service")
        print("CA: the host bridge uses the host operating-system trust store; no Docker CA overlay")
        print("Credentials: host Codex login stays on the host; the bridge token is read from the environment")
        print("Safety: the bridge is read-only and has no Docker socket or host-filesystem mount")
        if mode == "host-memory":
            print("Memory wiring: codex,ollama and host.docker.internal:8791 are set for Compose")
    print("Login:", "requested" if login else "not requested")
    print("Final plan:")
    for index, step in enumerate(steps, start=1):
        suffix = " (background)" if step.background else ""
        print(f"  {index}. {step.description}{suffix}")
        print(f"     {_format_command(step.command)}")
    if args.dry_run:
        return 0
    if not args.execute:
        if args.mode is None and _ask_yes_no("Execute this plan now?", default=True):
            pass
        else:
            print("Preview only; rerun with --execute to start it.")
            return 0
    try:
        return _execute_plan(mode, steps)
    except (OSError, RuntimeError) as error:
        print(f"Launch failed: {error}", file=sys.stderr)
        return 1
