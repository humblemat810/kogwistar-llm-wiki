"""Compose generation and validation commands for the LLM-Wiki CLI."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from ..compose_config import ComposeOptions, check_compose_text, write_compose


def compose_options_from_args(args: argparse.Namespace) -> ComposeOptions:
    return ComposeOptions(
        backend=args.backend,
        workspace=args.workspace,
        project_name=args.project_name,
        mode=args.mode,
        embedding_backend=args.embedding_backend,
        with_otel=args.with_otel,
        with_oauth=args.with_oauth,
        auth_mode=args.auth_mode,
        model_revision=args.model_revision,
        embedding_dimension=args.embedding_dimension,
    )


def compose_generate(args: argparse.Namespace) -> None:
    path = write_compose(args.output, compose_options_from_args(args))
    print(json.dumps({"status": "generated", "path": str(path.resolve())}, indent=2))


def compose_check(args: argparse.Namespace) -> None:
    path = Path(args.file)
    result = check_compose_text(path.read_text(encoding="utf-8"))
    docker_check: dict[str, object] = {"status": "not_run", "detail": "docker command unavailable"}
    try:
        completed = subprocess.run(
            ["docker", "compose", "-f", str(path), "config", "--quiet"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            check=False,
        )
    except OSError as exc:
        docker_check["detail"] = f"docker compose check skipped: {exc}"
    else:
        if completed.returncode == 0:
            docker_check = {"status": "passed"}
        else:
            docker_check = {
                "status": "failed",
                "detail": (completed.stderr or completed.stdout).strip()[:2000],
            }
            result["valid"] = False
            errors = result.setdefault("errors", [])
            if isinstance(errors, list):
                errors.append("docker compose config rejected the file")
    result["docker_compose"] = docker_check
    print(json.dumps({"file": str(path), **result}, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(78)
