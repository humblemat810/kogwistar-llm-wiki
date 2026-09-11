"""Run the standalone service with Uvicorn."""

from __future__ import annotations

import os


def main() -> int:
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(), host=os.getenv("LLM_WIKI_EMBEDDING_HOST", "0.0.0.0"), port=int(os.getenv("LLM_WIKI_EMBEDDING_PORT", "8790")), log_level=os.getenv("LLM_WIKI_EMBEDDING_LOG_LEVEL", "info"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
