#!/usr/bin/env python3
"""Run the shared provider-free PyPy 3.11 compatibility profile.

The GitHub job supplies the interpreter and dependencies.  The local Linux
container may additionally ask this runner to create a PyPy-owned virtual
environment and install the same bounded profile before running the checks.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PYTEST_MARKERS = (
    "ci and not ci_full and not slow and not manual and not llm_real and "
    "not longrun and not requires_ollama and not requires_chroma"
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(command: list[str], *, env: dict[str, str], log_path: Path | None = None) -> int:
    print("$", " ".join(command), flush=True)
    if log_path is None:
        return subprocess.call(command, cwd=_root(), env=env)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=_root(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        return process.wait()


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _reexec_in_venv(args: argparse.Namespace) -> int | None:
    if args.venv is None or args.venv_active:
        return None
    venv = args.venv.resolve()
    if not venv.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)], cwd=_root())
    python = _venv_python(venv)
    forwarded = [str(Path(__file__).resolve()), "--venv-active"]
    if args.skip_install:
        forwarded.append("--skip-install")
    if args.results_dir is not None:
        forwarded.extend(["--results-dir", str(args.results_dir)])
    env = os.environ.copy()
    return subprocess.call([str(python), *forwarded], cwd=_root(), env=env)


def _install_profile(python: str, env: dict[str, str]) -> None:
    subprocess.check_call([python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], cwd=_root(), env=env)
    subprocess.check_call(
        [python, "-m", "pip", "install", "--no-deps", "--ignore-requires-python", "-e", "."],
        cwd=_root(),
        env=env,
    )
    subprocess.check_call(
        [python, "-m", "pip", "install", "-r", "requirements/pypy-3.11-experimental.txt"],
        cwd=_root(),
        env=env,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, help="Create and use a PyPy-owned virtual environment first.")
    parser.add_argument("--venv-active", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-install", action="store_true", help="Use dependencies already installed in the active interpreter.")
    parser.add_argument("--results-dir", type=Path, default=Path("test-results"))
    args = parser.parse_args()

    reexec_status = _reexec_in_venv(args)
    if reexec_status is not None:
        return reexec_status

    root = _root()
    results = (root / args.results_dir).resolve()
    results.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    source_paths = [root / name for name in ("kogwistar", "kg-doc-parser", "kogwistar-obsidian-sink", "src")]
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in source_paths) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    env.setdefault("KOGWISTAR_IMPL_MODE", "python")
    env.setdefault("LLM_WIKI_AUTH_MODE", "disabled")
    env.setdefault("LLM_WIKI_OTEL_ENABLED", "false")
    env.setdefault("KOGWISTAR_LOG_LEVEL", "WARNING")
    env.setdefault("LOG_LEVEL", "WARNING")

    implementation = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.implementation); print(sys.version)"],
        cwd=root,
        env=env,
        check=False,
    )
    if implementation.returncode != 0:
        return implementation.returncode
    if sys.implementation.name != "pypy" or sys.version_info[:2] != (3, 11):
        print("This profile requires PyPy 3.11", file=sys.stderr)
        return 2

    if not args.skip_install:
        _install_profile(sys.executable, env)

    profile_path = results / "pypy-profile-pypy311.json"
    profile_status = _run(
        [
            sys.executable,
            "scripts/check_pypy_profile.py",
            "--expected-python",
            "3.11",
            "--json",
            "--require-import",
            "kogwistar",
            "--require-import",
            "kogwistar_llm_wiki",
            "--probe-import",
            "mcp.server.lowlevel",
        ],
        env=env,
        log_path=profile_path,
    )
    if profile_status != 0:
        return profile_status

    pytest_log = results / "pytest-pypy311.log"
    return _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            PYTEST_MARKERS,
            "tests",
            "-q",
            "--durations=25",
            "--durations-min=0",
            "--resource-report",
            "--resource-report-json",
            str(results / "resource-report-pypy311.json"),
            "-p",
            "no:cacheprovider",
        ],
        env=env,
        log_path=pytest_log,
    )


if __name__ == "__main__":
    raise SystemExit(main())
