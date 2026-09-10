"""Run the isolated representation service with Uvicorn."""

from __future__ import annotations

import os


def main() -> int:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise SystemExit(
            "Install the representation-service extra before starting this service"
        ) from exc
    from .app import create_app

    uvicorn.run(
        create_app(),
        host=os.environ.get("LLM_WIKI_REPRESENTATION_HOST", "0.0.0.0"),
        port=int(os.environ.get("LLM_WIKI_REPRESENTATION_PORT", "8790")),
        log_level=os.environ.get("LLM_WIKI_REPRESENTATION_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
