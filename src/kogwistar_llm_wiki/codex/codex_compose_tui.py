"""Guided terminal launcher for Codex and the LLM-Wiki memory stack."""

from __future__ import annotations

import argparse
import json
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
StackMode = Literal["split", "combined"]
EmbeddingMode = Literal["none", "vllm", "transformers"]


@dataclass(frozen=True)
class LaunchStep:
    """One visible process action in a guided launch plan."""

    description: str
    command: list[str]
    background: bool = False
    environment: dict[str, str] | None = None


@dataclass(frozen=True)
class TuiConfiguration:
    """Non-secret deployment settings collected by the guided launcher."""

    stack: StackMode = "split"
    embedding: EmbeddingMode = "none"
    maintenance_enabled: bool = False
    request_enabled: bool = True
    background_enabled: bool = False
    maintenance_profile: str = "balanced"
    profile_ladder_json: str | None = None
    combined_memory_limit: str = "512m"
    combined_cpu_limit: str = "0.25"
    embedding_dimension: int = 1024
    embedding_max_model_len: int = 8192
    embedding_crop_token_budget: int = 7680
    env_file: str = ".env"

    def __post_init__(self) -> None:
        if self.maintenance_profile not in {"high", "balanced", "budgeted", "lite"}:
            raise ValueError("maintenance profile must be high, balanced, budgeted, or lite")
        if self.embedding_dimension < 1:
            raise ValueError("embedding dimension must be positive")
        if self.embedding_max_model_len < 1:
            raise ValueError("embedding max model length must be positive")
        if not 1 <= self.embedding_crop_token_budget <= self.embedding_max_model_len:
            raise ValueError("embedding crop token budget must be between 1 and max model length")
        if self.profile_ladder_json not in (None, ""):
            try:
                ladder = json.loads(self.profile_ladder_json)
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError("profile ladder must be valid JSON") from error
            from ..maintenance import normalize_profile_ladder

            normalize_profile_ladder(ladder)


def configuration_environment(configuration: TuiConfiguration) -> dict[str, str]:
    """Return the allowlisted, non-secret environment managed by the TUI."""
    values = {
        "LLM_WIKI_MAINTENANCE_ENABLED": str(configuration.maintenance_enabled).lower(),
        "LLM_WIKI_MAINTENANCE_REQUEST_ENABLED": str(configuration.request_enabled).lower(),
        "LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED": str(configuration.background_enabled).lower(),
        "LLM_WIKI_MAINTENANCE_PROFILE": configuration.maintenance_profile,
        "LLM_WIKI_COMBINED_MEMORY_LIMIT": configuration.combined_memory_limit,
        "LLM_WIKI_COMBINED_CPU_LIMIT": configuration.combined_cpu_limit,
        "LLM_WIKI_EMBEDDING_DIMENSION": str(configuration.embedding_dimension),
        "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN": str(configuration.embedding_max_model_len),
        "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET": str(configuration.embedding_crop_token_budget),
    }
    if configuration.profile_ladder_json is not None:
        values["LLM_WIKI_MAINTENANCE_PROFILE_LADDER"] = configuration.profile_ladder_json
    return values


def merge_configuration_env(path: str | os.PathLike[str], configuration: TuiConfiguration) -> None:
    """Update only TUI-owned keys, preserving secrets and unrelated env values."""
    target = Path(path)
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    values = configuration_environment(configuration)
    seen: set[str] = set()
    lines: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(line)
    if lines and original.endswith(("\n", "\r")):
        ending = "\n"
    else:
        ending = "\n"
    lines.extend(f"{key}={values[key]}" for key in values if key not in seen)
    target.write_text("\n".join(lines) + ending, encoding="utf-8")


def _read_env_defaults(path: str | os.PathLike[str]) -> dict[str, str]:
    """Read simple dotenv assignments; secrets are not displayed or returned."""
    values: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() in {
            "LLM_WIKI_MAINTENANCE_ENABLED",
            "LLM_WIKI_MAINTENANCE_REQUEST_ENABLED",
            "LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED",
            "LLM_WIKI_MAINTENANCE_PROFILE",
            "LLM_WIKI_MAINTENANCE_PROFILE_LADDER",
            "LLM_WIKI_COMBINED_MEMORY_LIMIT",
            "LLM_WIKI_COMBINED_CPU_LIMIT",
            "LLM_WIKI_EMBEDDING_DIMENSION",
            "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN",
            "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET",
        }:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


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


def _ask_choice(prompt: str, choices: tuple[str, ...], default: str) -> str:
    allowed = "/".join(choices)
    while True:
        answer = input(f"{prompt} [{allowed}] (default {default}): ").strip().lower() or default
        if answer in choices:
            return answer
        print(f"Please choose one of: {allowed}.")


def _ask_integer(prompt: str, default: int) -> int:
    while True:
        answer = input(f"{prompt} (default {default}): ").strip()
        try:
            return int(answer) if answer else default
        except ValueError:
            print("Please enter a whole number.")


def _configure_interactively(env_file: str) -> TuiConfiguration:
    """Collect operational knobs without ever asking for credentials."""
    defaults = _read_env_defaults(env_file)
    stack = _ask_choice("Stack layout", ("split", "combined"), "split")
    embedding = _ask_choice("Embedding service", ("none", "vllm", "transformers"), "none")
    maintenance_enabled = _ask_yes_no(
        "Enable maintenance daemon",
        default=defaults.get("LLM_WIKI_MAINTENANCE_ENABLED", "false").lower() == "true",
    )
    request_enabled = _ask_yes_no(
        "Enable request-bounded maintenance",
        default=defaults.get("LLM_WIKI_MAINTENANCE_REQUEST_ENABLED", "true").lower() == "true",
    )
    background_enabled = _ask_yes_no(
        "Enable background maintenance",
        default=defaults.get("LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED", "false").lower() == "true",
    )
    profile = _ask_choice(
        "Maintenance profile",
        ("high", "balanced", "budgeted", "lite"),
        defaults.get("LLM_WIKI_MAINTENANCE_PROFILE", "balanced"),
    )
    ladder_default = defaults.get("LLM_WIKI_MAINTENANCE_PROFILE_LADDER", "[]")
    print("Optional ordered profile ladder JSON; use [] to disable it.")
    ladder = input(f"Profile ladder (default {ladder_default}): ").strip() or ladder_default
    memory = input(
        f"Combined service memory limit (default {defaults.get('LLM_WIKI_COMBINED_MEMORY_LIMIT', '512m')}): "
    ).strip() or defaults.get("LLM_WIKI_COMBINED_MEMORY_LIMIT", "512m")
    cpu = input(
        f"Combined service CPU limit (default {defaults.get('LLM_WIKI_COMBINED_CPU_LIMIT', '0.25')}): "
    ).strip() or defaults.get("LLM_WIKI_COMBINED_CPU_LIMIT", "0.25")
    dimension = _ask_integer("Embedding dimension", int(defaults.get("LLM_WIKI_EMBEDDING_DIMENSION", "1024")))
    max_model_len = _ask_integer("Embedding max model length", int(defaults.get("LLM_WIKI_EMBEDDING_MAX_MODEL_LEN", "8192")))
    crop_budget = _ask_integer(
        "Embedding token crop budget",
        int(defaults.get("LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET", str(min(7680, max_model_len)))),
    )
    try:
        return TuiConfiguration(
            stack=stack,  # type: ignore[arg-type]
            embedding=embedding,  # type: ignore[arg-type]
            maintenance_enabled=maintenance_enabled,
            request_enabled=request_enabled,
            background_enabled=background_enabled,
            maintenance_profile=profile,
            profile_ladder_json=ladder,
            combined_memory_limit=memory,
            combined_cpu_limit=cpu,
            embedding_dimension=dimension,
            embedding_max_model_len=max_model_len,
            embedding_crop_token_budget=crop_budget,
            env_file=env_file,
        )
    except ValueError as error:
        raise RuntimeError(str(error)) from error


def _profile_ladder_from_file(path: str | None) -> str | None:
    """Load ladder JSON from a file for shells that mangle inline JSON quotes."""
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"cannot read profile ladder file {path}: {error}") from error


def _compose_launcher(
    mode: Literal["standalone", "memory"],
    *,
    build: bool,
    login: bool,
    project: str,
    configuration: TuiConfiguration,
) -> list[str]:
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
            "-Stack",
            configuration.stack,
            "-Embedding",
            configuration.embedding,
            "-EnvFile",
            configuration.env_file,
        ]
        if build:
            command.append("-Build")
        if login:
            command.append("-Login")
        return command
    command = [
        "bash",
        str(root / "scripts" / "start_codex_compose.sh"),
        "--mode",
        mode,
        "--project",
        project,
        "--stack",
        configuration.stack,
        "--embedding",
        configuration.embedding,
        "--env-file",
        configuration.env_file,
    ]
    if build:
        command.append("--build")
    if login:
        command.append("--login")
    return command


def _host_bridge_command() -> list[str]:
    return [sys.executable, "-m", "kogwistar_llm_wiki", "codex-bridge", "--host", "0.0.0.0", "--port", "8791"]


def _lxc_preflight_command(*, apply: bool) -> list[str]:
    root = _repository_root()
    return ["bash", str(root / "scripts" / "setup_lxc_docker.sh"), "--apply" if apply else "--check"]


def _host_memory_compose_command(project: str, configuration: TuiConfiguration) -> list[str]:
    command = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        "compose.yml",
        "-f",
        "compose.memory-agent.yml",
    ]
    if configuration.stack == "combined":
        command.extend(["-f", "compose.combined.yml"])
    if configuration.embedding == "vllm":
        command.extend(["-f", "compose.embedding-vllm.yml"])
    elif configuration.embedding == "transformers":
        command.extend(["-f", "compose.multimodal.yml"])
    command.extend(["up", "-d"])
    return command


def build_plan(
    mode: LaunchMode,
    *,
    build: bool,
    login: bool,
    project: str,
    configuration: TuiConfiguration | None = None,
    lxc: bool = False,
    lxc_apply: bool = False,
) -> list[LaunchStep]:
    """Build the exact visible process steps for a selected deployment mode."""
    configuration = configuration or TuiConfiguration()
    steps: list[LaunchStep] = []
    if lxc:
        steps.append(
            LaunchStep(
                "check Docker prerequisites inside the Linux LXC",
                _lxc_preflight_command(apply=lxc_apply),
            )
        )
    if mode in {"standalone", "memory"}:
        steps.append(
            LaunchStep(
                "start the CA-aware Codex Compose launcher",
                _compose_launcher(
                    mode,
                    build=build,
                    login=login,
                    project=project,
                    configuration=configuration,
                ),
                environment=configuration_environment(configuration),
            )
        )
        return steps
    if login:
        steps.append(LaunchStep("authenticate the host Codex CLI with its normal SSO flow", ["codex", "login"]))
    steps.append(LaunchStep("start the host Codex bridge", _host_bridge_command(), background=True))
    if mode == "host-memory":
        steps.append(
            LaunchStep(
                "start the memory stack using the host bridge",
                _host_memory_compose_command(project, configuration),
                environment=configuration_environment(configuration),
            )
        )
    return steps


def _format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)


def _host_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment["KOGWISTAR_MAINTENANCE_PROVIDER"] = "codex"
    environment["KOGWISTAR_MAINTENANCE_PROVIDER_CHAIN"] = "codex,ollama"
    environment["KOGWISTAR_MAINTENANCE_CODEX_BASE_URL"] = "http://host.docker.internal:8791"
    environment["KOGWISTAR_MAINTENANCE_CODEX_API_KEY_ENV"] = "LLM_WIKI_CODEX_BRIDGE_TOKEN"
    if extra:
        environment.update(extra)
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
            process = subprocess.Popen(step.command, cwd=_repository_root(), env=_host_environment(step.environment))
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
            env=_host_environment(step.environment) if mode == "host-memory" else (
                {**os.environ, **(step.environment or {})} if step.environment else None
            ),
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
    parser.add_argument("--configure", action="store_true", help="Interactively configure deployment settings")
    parser.add_argument("--stack", choices=["split", "combined"], default="split")
    parser.add_argument("--embedding", choices=["none", "vllm", "transformers"], default="none")
    parser.add_argument("--maintenance-enabled", choices=["true", "false"], default="false")
    parser.add_argument("--request-enabled", choices=["true", "false"], default="true")
    parser.add_argument("--background-enabled", choices=["true", "false"], default="false")
    parser.add_argument("--maintenance-profile", choices=["high", "balanced", "budgeted", "lite"], default="balanced")
    parser.add_argument(
        "--profile-ladder",
        "--profile-ladder-json",
        dest="profile_ladder_json",
        default=None,
        help="Ordered JSON provider/profile quota ladder",
    )
    parser.add_argument(
        "--profile-ladder-file",
        default=None,
        help="Read ordered profile ladder JSON from a file (useful on Windows shells)",
    )
    parser.add_argument("--combined-memory", default="512m")
    parser.add_argument("--combined-cpu", default="0.25")
    parser.add_argument("--embedding-dimension", type=int, default=1024)
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--crop-token-budget", type=int, default=7680)
    parser.add_argument("--env-file", default=".env", help="Env file used by Compose and optional configuration output")
    parser.add_argument("--write-env", action="store_true", help="Persist only non-secret settings to --env-file")
    parser.add_argument("--lxc", action="store_true", help="Run the Linux LXC Docker preflight before Compose")
    parser.add_argument("--lxc-apply", action="store_true", help="Install Docker in the LXC; implies --lxc and requires root")
    parser.add_argument("--build", action="store_true", help="Build the Codex image before starting")
    parser.add_argument("--login", action="store_true", help="Run authentication before starting")
    parser.add_argument("--execute", action="store_true", help="Execute after showing the selected plan")
    parser.add_argument("--dry-run", action="store_true", help="Show the selected plan without running it")
    args = parser.parse_args(argv)

    mode = args.mode or _choose_mode()
    if args.lxc_apply:
        args.lxc = True
    if args.lxc and os.name == "nt":
        parser.error("--lxc is available only when this TUI runs on a Linux host/LXC")
    container_mode = mode in {"standalone", "memory"}
    build = args.build or (not args.mode and container_mode and _ask_yes_no("Build the Codex image first?"))
    login = args.login or (not args.mode and _ask_yes_no("Run authentication first?"))
    if not container_mode and build:
        parser.error("--build applies only to container modes")
    try:
        interactive_configuration = args.configure
        if args.mode is None and not interactive_configuration:
            interactive_configuration = _ask_yes_no("Configure stack settings before launch?", default=False)
        if interactive_configuration:
            configuration = _configure_interactively(args.env_file)
        else:
            profile_ladder_json = args.profile_ladder_json
            if args.profile_ladder_file:
                if profile_ladder_json:
                    parser.error("use only one of --profile-ladder and --profile-ladder-file")
                profile_ladder_json = _profile_ladder_from_file(args.profile_ladder_file)
            configuration = TuiConfiguration(
                stack=args.stack,
                embedding=args.embedding,
                maintenance_enabled=args.maintenance_enabled == "true",
                request_enabled=args.request_enabled == "true",
                background_enabled=args.background_enabled == "true",
                maintenance_profile=args.maintenance_profile,
                profile_ladder_json=profile_ladder_json,
                combined_memory_limit=args.combined_memory,
                combined_cpu_limit=args.combined_cpu,
                embedding_dimension=args.embedding_dimension,
                embedding_max_model_len=args.max_model_len,
                embedding_crop_token_budget=args.crop_token_budget,
                env_file=args.env_file,
            )
        steps = build_plan(
            mode,
            build=build,
            login=login,
            project=args.project,
            configuration=configuration,
            lxc=args.lxc,
            lxc_apply=args.lxc_apply,
        )
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
    print("Stack:", configuration.stack)
    print("Embedding:", configuration.embedding)
    print(
        "Maintenance:",
        f"master={'on' if configuration.maintenance_enabled else 'off'}, "
        f"request={'on' if configuration.request_enabled else 'off'}, "
        f"background={'on' if configuration.background_enabled else 'off'}, "
        f"profile={configuration.maintenance_profile}",
    )
    print(
        "Limits:",
        f"combined={configuration.combined_memory_limit}/{configuration.combined_cpu_limit}, "
        f"embedding={configuration.embedding_dimension}d/{configuration.embedding_max_model_len} tokens "
        f"(crop {configuration.embedding_crop_token_budget})",
    )
    if args.write_env:
        print(f"Env update: allowlisted non-secret settings in {configuration.env_file}")
    if args.lxc:
        print("LXC: preflight requested; --lxc-apply installs Docker only after explicit execution")
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
    if args.write_env:
        try:
            merge_configuration_env(configuration.env_file, configuration)
        except OSError as error:
            print(f"Could not write {configuration.env_file}: {error}", file=sys.stderr)
            return 1
    try:
        return _execute_plan(mode, steps)
    except (OSError, RuntimeError) as error:
        print(f"Launch failed: {error}", file=sys.stderr)
        return 1
