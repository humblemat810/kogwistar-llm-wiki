"""Container startup gate for the llm-wiki image."""

from __future__ import annotations

import os
import sys

from .ingest_pipeline import _resolve_embedding_functions


def main(argv: list[str] | None = None) -> int:
    command = list(argv if argv is not None else sys.argv[1:])
    if not command:
        print("llm-wiki container: no command configured", file=sys.stderr)
        return 64

    try:
        _resolve_embedding_functions()
    except Exception as exc:
        print(
            "llm-wiki container configuration invalid: "
            f"{exc}\n"
            "Update the environment variables, then recreate the app container "
            "with `docker compose up -d --force-recreate` "
            "(`--build` is only needed after changing application code or the Dockerfile).",
            file=sys.stderr,
        )
        return 78

    os.execvpe(command[0], command, os.environ)
    return 0  # pragma: no cover - os.execvpe replaces the process


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
