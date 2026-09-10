"""Container startup gate for the llm-wiki image."""

from __future__ import annotations

import os
import sys

from .ingest_pipeline import _resolve_embedding_functions
from .multimodal_runtime import (
    configured_multimodal_backend,
    configured_representation_service_url,
    validate_configured_multimodal_runtime,
)


def main(argv: list[str] | None = None) -> int:
    command = list(argv if argv is not None else sys.argv[1:])
    if not command:
        print("llm-wiki container: no command configured", file=sys.stderr)
        return 64

    try:
        _resolve_embedding_functions()
        if configured_representation_service_url():
            if configured_multimodal_backend() != "none":
                raise RuntimeError(
                    "local multimodal model settings cannot be combined with "
                    "LLM_WIKI_REPRESENTATION_SERVICE_URL; use the remote service profile"
                )
        else:
            if configured_multimodal_backend() != "none":
                raise RuntimeError(
                    "local multimodal inference is not supported in the production app container; "
                    "set LLM_WIKI_REPRESENTATION_SERVICE_URL and an explicit "
                    "LLM_WIKI_REPRESENTATION_SERVICE_ALLOWED_HOSTS allowlist, or disable "
                    "LLM_WIKI_MULTIMODAL_BACKEND"
                )
            # The base app image is Torch-free. Local adapters remain available
            # only when constructed directly by developer/test code.
            validate_configured_multimodal_runtime(require_device=True)
    except Exception as exc:
        print(
            "llm-wiki container configuration invalid: "
            f"{exc}\n"
            "Update the environment variables, then recreate the app container "
            "with `docker compose up -d --force-recreate` "
            "(`--build` is required after changing the multimodal Torch profile, "
            "application code, or the Dockerfile).",
            file=sys.stderr,
        )
        return 78

    os.execvpe(command[0], command, os.environ)
    return 0  # pragma: no cover - os.execvpe replaces the process


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
