"""FastAPI application for one Qwen3-VL dense representation profile."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from hashlib import sha256
import os
from typing import Any

try:  # Keep importing LLM-Wiki itself Torch/FastAPI-free.
    from fastapi import Request as FastAPIRequest
except ImportError:  # pragma: no cover - exercised in base installations
    FastAPIRequest = Any  # type: ignore[assignment,misc]

from ..multimodal_projection import (
    MultimodalSourceUnit,
    ProjectionIntegrityError,
    Qwen3VLDenseEncoder,
)
from ..multimodal_sources import MappingAssetResolver
from .config import RepresentationServiceConfig, load_config


def _profile_payload(config: RepresentationServiceConfig) -> dict[str, object]:
    return config.profile.canonical_payload() | {"fingerprint": config.profile.fingerprint}


def _error(message: str, status: int) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse({"error": message}, status_code=status)


def create_app(
    *,
    encoder: Any | None = None,
    config: RepresentationServiceConfig | None = None,
) -> Any:
    """Build the service app, lazily importing FastAPI and model dependencies."""

    try:
        from fastapi import FastAPI
    except ImportError as exc:  # pragma: no cover - exercised by packaging tests
        raise RuntimeError(
            "representation service requires the optional 'representation-service' extra"
        ) from exc

    selected = config or load_config()
    state: dict[str, Any] = {"encoder": encoder, "config": selected, "load_error": None}
    if encoder is not None and getattr(encoder, "profile", None) != selected.profile:
        raise ValueError("injected encoder profile does not match representation service configuration")

    @asynccontextmanager
    async def lifespan(_app: Any):
        if state["encoder"] is None:
            try:
                from ..multimodal_runtime import validate_torch_runtime

                validate_torch_runtime(
                    selected.torch_backend, require_device=selected.device == "cuda"
                )
                state["encoder"] = Qwen3VLDenseEncoder.from_pretrained(
                    selected.model,
                    revision=selected.revision,
                    device=selected.device,
                    batch_size=selected.batch_size,
                    dimension=selected.dimension,
                    instruction=selected.instruction,
                )
            except Exception as exc:  # readiness reports the failure without import-time crash
                state["load_error"] = str(exc)
        yield

    app = FastAPI(title="LLM-Wiki Representation Service", version="1", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "service": "llm-wiki-representation"}

    @app.get("/readyz")
    def readyz() -> Any:
        if state["encoder"] is None:
            return _error(state["load_error"] or "model is not loaded", 503)
        return {"ready": True, "profile": _profile_payload(selected)}

    @app.get("/v1/capabilities")
    def capabilities(request: FastAPIRequest) -> Any:
        if not _authorized(request, selected.token):
            return _error("unauthorized", 401)
        return {
            "contract_version": "v1",
            "service": "llm-wiki-representation",
            "representation": "dense",
            "modalities": ["text", "image", "pdf_page", "table", "chart", "video_frame", "webpage"],
            "profile": _profile_payload(selected),
            "max_items": selected.max_items,
            "max_request_bytes": selected.max_request_bytes,
        }

    @app.post("/v1/represent")
    async def represent(request: FastAPIRequest) -> Any:
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
            result = _represent_payload(payload, state["encoder"], selected)
            return result
        except ProjectionIntegrityError as exc:
            return _error(str(exc), 422)
        except ValueError as exc:
            return _error(str(exc), 400)

    return app


def _authorized(request: Any, token: str | None) -> bool:
    if not token:
        return True
    return request.headers.get("authorization", "") == f"Bearer {token}"


def _represent_payload(
    payload: object,
    encoder: Any,
    config: RepresentationServiceConfig,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("request must be an object")
    if payload.get("contract_version") != "v1":
        raise ValueError("unsupported representation contract version")
    operation = payload.get("operation")
    if operation not in {"query", "document"}:
        raise ValueError("operation must be query or document")
    if payload.get("profile_fingerprint") != config.profile.fingerprint:
        raise ValueError("requested profile fingerprint does not match this service")
    raw_items = payload.get("items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise ValueError("items must be a sequence")
    if not raw_items or len(raw_items) > config.max_items:
        raise ValueError(f"items must contain between 1 and {config.max_items} entries")
    item_ids: list[str] = []
    units: list[MultimodalSourceUnit] = []
    assets: dict[str, bytes] = {}
    text_queries: list[tuple[int, str]] = []
    image_queries: list[tuple[int, bytes]] = []
    mixed_query_units: list[tuple[int, MultimodalSourceUnit]] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise ValueError("each item must be an object")
        item_id = str(raw_item.get("item_id", ""))
        modality = str(raw_item.get("modality", "text"))
        if not item_id or item_id in item_ids:
            raise ValueError("item IDs must be non-empty and unique")
        item_ids.append(item_id)
        text = str(raw_item["text"]) if raw_item.get("text") is not None else None
        asset = raw_item.get("asset")
        ref = None
        if asset is not None:
            if not isinstance(asset, Mapping):
                raise ValueError("asset must be an object")
            try:
                raw = base64.b64decode(str(asset["data_base64"]), validate=True)
            except (KeyError, ValueError) as exc:
                raise ValueError("asset data_base64 is invalid") from exc
            digest = sha256(raw).hexdigest()
            if str(asset.get("sha256", "")).lower() != digest:
                raise ValueError(f"asset hash mismatch for item {item_id!r}")
            if not str(asset.get("content_type", "")).startswith(
                ("image/", "application/pdf", "video/")
            ):
                raise ValueError("asset content_type must be image/*, video/*, or application/pdf")
            ref = f"asset://{index}"
            assets[ref] = raw
        if operation == "query":
            if asset is not None:
                if text is not None:
                    mixed_query_units.append(
                        (
                            index,
                            MultimodalSourceUnit(
                                view_id=item_id,
                                workspace_id="remote",
                                source_id="remote",
                                source_revision_id="remote",
                                modality=modality,  # type: ignore[arg-type]
                                locator={"kind": "remote_query_item"},
                                content_ref=ref,
                                text=text,
                            ),
                        )
                    )
                else:
                    image_queries.append((index, assets[ref]))  # type: ignore[index]
            elif text is not None:
                text_queries.append((index, text))
            else:
                raise ValueError("query item requires text or asset")
        else:
            units.append(
                MultimodalSourceUnit(
                    view_id=item_id,
                    workspace_id="remote",
                    source_id="remote",
                    source_revision_id="remote",
                    modality=modality,  # type: ignore[arg-type]
                    locator={"kind": "remote_item"},
                    content_ref=ref,
                    text=text,
                )
            )
    if operation == "query":
        vectors_by_index: dict[int, object] = {}
        if text_queries:
            text_vectors = encoder.encode_queries([text for _, text in text_queries])
            vectors_by_index.update(
                {index: vector for (index, _), vector in zip(text_queries, text_vectors)}
            )
        if image_queries:
            image_vectors = encoder.encode_image_queries([raw for _, raw in image_queries])
            vectors_by_index.update(
                {index: vector for (index, _), vector in zip(image_queries, image_vectors)}
            )
        if mixed_query_units:
            mixed_vectors = encoder.encode_documents(
                [unit for _, unit in mixed_query_units],
                resolver=MappingAssetResolver(assets),
            )
            vectors_by_index.update(
                {index: vector for (index, _), vector in zip(mixed_query_units, mixed_vectors)}
            )
        if len(vectors_by_index) != len(item_ids):
            raise ProjectionIntegrityError("encoder returned a different number of query vectors")
        vectors = [vectors_by_index[index] for index in range(len(item_ids))]
    else:
        vectors = encoder.encode_documents(units, resolver=MappingAssetResolver(assets))
    if len(vectors) != len(item_ids):
        raise ProjectionIntegrityError("encoder returned a different number of vectors")
    return {
        "contract_version": "v1",
        "request_id": str(payload.get("request_id", "")),
        "profile": _profile_payload(config),
        "results": [
            {"item_id": item_id, "vectors": [list(vector) for vector in embedding_set]}
            for item_id, embedding_set in zip(item_ids, vectors)
        ],
    }


__all__ = ["create_app"]
