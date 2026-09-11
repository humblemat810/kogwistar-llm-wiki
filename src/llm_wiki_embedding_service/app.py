"""FastAPI application for one standalone Qwen3-VL profile."""

from __future__ import annotations

import base64
import asyncio
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from hashlib import sha256
import hmac
from typing import Any

from fastapi import FastAPI, Request

from llm_wiki_embedding_contract import ContractValidationError, validate_asset_bytes, validate_dense_vectors

from .config import EmbeddingServiceConfig, load_config
from .encoder import Qwen3VLDenseEncoder


def _profile(config: EmbeddingServiceConfig) -> dict[str, object]:
    return config.profile.canonical_payload() | {"fingerprint": config.profile.fingerprint}


def _error(message: str, status: int) -> Any:
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": message}, status_code=status)


def create_app(*, encoder: Any | None = None, config: EmbeddingServiceConfig | None = None) -> Any:
    selected = config or load_config()
    if encoder is not None and getattr(encoder, "profile", None) != selected.profile:
        raise ValueError("injected encoder profile does not match service configuration")
    state: dict[str, Any] = {"encoder": encoder, "load_error": None}

    @asynccontextmanager
    async def lifespan(_app: Any):
        load_task: asyncio.Task[None] | None = None
        if state["encoder"] is None:
            async def load_model() -> None:
                try:
                    state["encoder"] = await asyncio.to_thread(
                        Qwen3VLDenseEncoder.from_pretrained, selected
                    )
                except Exception as exc:  # readiness reports model failures
                    state["load_error"] = str(exc)

            load_task = asyncio.create_task(load_model())
        yield
        if load_task is not None and not load_task.done():
            load_task.cancel()
            await asyncio.gather(load_task, return_exceptions=True)

    app = FastAPI(title="LLM-Wiki Embedding Service", version="1", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "service": "llm-wiki-embedding"}

    @app.get("/readyz")
    def readyz() -> Any:
        if state["encoder"] is None:
            return _error(state["load_error"] or "model is not loaded", 503)
        return {"ready": True, "profile": _profile(selected)}

    @app.get("/v1/capabilities")
    def capabilities(request: Request) -> Any:
        if not _authorized(request, selected.token):
            return _error("unauthorized", 401)
        return {"contract_version": "v1", "service": "llm-wiki-embedding", "embedding": "dense", "modalities": ["text", "image", "pdf_page", "table", "chart", "video_frame", "webpage"], "profile": _profile(selected), "batch_size": selected.batch_size, "max_items": selected.max_items, "max_request_bytes": selected.max_request_bytes}

    @app.post("/v1/represent")
    async def represent(request: Request) -> Any:
        if not _authorized(request, selected.token):
            return _error("unauthorized", 401)
        if state["encoder"] is None:
            return _error(state["load_error"] or "model is not ready", 503)
        try:
            content_length = int(request.headers.get("content-length", "0"))
            if content_length > selected.max_request_bytes:
                return _error("request exceeds configured byte limit", 413)
            body = await request.body()
            if len(body) > selected.max_request_bytes:
                return _error("request exceeds configured byte limit", 413)
            payload = await request.json()
            return _represent_payload(payload, state["encoder"], selected)
        except ContractValidationError as exc:
            return _error(str(exc), 422)
        except ValueError as exc:
            return _error(str(exc), 400)

    return app


def _authorized(request: Any, token: str | None) -> bool:
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {token}" if token else ""
    return not token or hmac.compare_digest(supplied, expected)


def _represent_payload(payload: object, encoder: Any, config: EmbeddingServiceConfig) -> dict[str, object]:
    if not isinstance(payload, Mapping) or payload.get("contract_version") != "v1":
        raise ContractValidationError("unsupported embedding contract version")
    if payload.get("operation") not in {"query", "document"}:
        raise ContractValidationError("operation must be query or document")
    if payload.get("profile_fingerprint") != config.profile.fingerprint:
        raise ContractValidationError("requested profile fingerprint does not match this service")
    raw_items = payload.get("items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)) or not raw_items or len(raw_items) > config.max_items:
        raise ContractValidationError(f"items must contain between 1 and {config.max_items} entries")
    items: list[dict[str, object]] = []
    item_ids: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ContractValidationError("each item must be an object")
        item_id = str(raw_item.get("item_id", ""))
        if not item_id or item_id in item_ids:
            raise ContractValidationError("item IDs must be non-empty and unique")
        item_ids.add(item_id)
        item: dict[str, object] = {"item_id": item_id, "modality": str(raw_item.get("modality", "text"))}
        if raw_item.get("text") is not None:
            item["text"] = str(raw_item["text"])
        asset = raw_item.get("asset")
        if asset is not None:
            if not isinstance(asset, Mapping):
                raise ContractValidationError("asset must be an object")
            try:
                raw = base64.b64decode(str(asset["data_base64"]), validate=True)
                content_type = str(asset["content_type"])
                digest = str(asset["sha256"])
            except (KeyError, ValueError) as exc:
                raise ContractValidationError("asset is invalid") from exc
            item["asset"] = {"bytes": validate_asset_bytes(raw, content_type=content_type, expected_sha256=digest), "content_type": content_type}
        if "text" not in item and "asset" not in item:
            raise ContractValidationError(f"item {item_id!r} has no text or asset")
        items.append(item)
    vectors = encoder.encode(items)
    if len(vectors) != len(items):
        raise ContractValidationError("encoder returned a different number of vectors")
    results = []
    for item, embedding_set in zip(items, vectors):
        checked = validate_dense_vectors(embedding_set, dimension=config.dimension)
        results.append({"item_id": item["item_id"], "vectors": [list(vector) for vector in checked]})
    return {"contract_version": "v1", "request_id": str(payload.get("request_id", "")), "profile": _profile(config), "results": results}


__all__ = ["create_app", "_represent_payload"]
