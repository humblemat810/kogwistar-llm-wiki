#!/usr/bin/env python3
"""Build and install the optional root Rust extension for the current host."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "rust" / "crates" / "kogwistar-llm-wiki-python" / "Cargo.toml"
DEFAULT_TARGET_DIR = ROOT / "rust" / "target"
DEFAULT_DESTINATION = ROOT / "src" / "kogwistar_llm_wiki"
MODULE_NAME = "_kogwistar_llm_wiki_rust"


def cargo_artifact_name(platform_name: str) -> str:
    """Return Cargo's cdylib file name for the host platform."""

    if platform_name == "win32":
        return f"{MODULE_NAME}.dll"
    if platform_name == "darwin":
        return f"lib{MODULE_NAME}.dylib"
    return f"lib{MODULE_NAME}.so"


def python_artifact_name(platform_name: str) -> str:
    """Return the importable extension file name for the host platform."""

    if platform_name == "win32":
        return f"{MODULE_NAME}.pyd"
    return f"{MODULE_NAME}.so"


def _platform_name(explicit: str | None = None) -> str:
    return explicit or sys.platform


def find_cargo_artifact(
    target_dir: Path,
    profile: str,
    platform_name: str | None = None,
) -> Path:
    """Locate a Cargo cdylib artifact in the selected profile directory."""

    profile_dir = (
        target_dir
        if profile in {"debug", "release"}
        else target_dir / profile
    )
    artifact = profile_dir / cargo_artifact_name(_platform_name(platform_name))
    if not artifact.is_file():
        raise FileNotFoundError(f"native extension artifact was not built: {artifact}")
    return artifact


def install_native_artifact(
    target_dir: Path,
    profile: str,
    destination_dir: Path,
    *,
    platform_name: str | None = None,
    copier: Callable[[Path, Path], None] = shutil.copy2,
) -> Path:
    """Copy the Cargo artifact under Python's importable extension name."""

    source = find_cargo_artifact(target_dir, profile, platform_name)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / python_artifact_name(_platform_name(platform_name))
    copier(source, destination)
    return destination


def build_native(
    *,
    profile: str = "release",
    target_dir: Path | None = None,
    destination_dir: Path | None = None,
    cargo: str = "cargo",
    platform_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> Path:
    """Run Cargo, then install the importable extension next to the package."""

    runner = runner or subprocess.run
    selected_target = target_dir or DEFAULT_TARGET_DIR
    selected_destination = destination_dir or DEFAULT_DESTINATION
    command = [
        cargo,
        "build",
        "--manifest-path",
        str(MANIFEST),
        "--profile",
        profile,
        "--target-dir",
        str(selected_target),
    ]
    result = runner(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=getattr(result, "stdout", None),
            stderr=getattr(result, "stderr", None),
        )
    return install_native_artifact(
        selected_target,
        profile,
        selected_destination,
        platform_name=platform_name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="release")
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET_DIR)
    parser.add_argument("--destination-dir", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--cargo", default=os.getenv("CARGO", "cargo"))
    args = parser.parse_args()
    destination = build_native(
        profile=args.profile,
        target_dir=args.target_dir,
        destination_dir=args.destination_dir,
        cargo=args.cargo,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
