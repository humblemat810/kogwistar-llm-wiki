"""Authenticated HTTP adapter for the external multimodal service.

The adapter deliberately sends resolved asset bytes, never source paths or
URLs.  It implements the existing multimodal encoder protocols so Stage 1 and
Stage 2 storage remain owned by LLM-Wiki.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .multimodal_projection import (
    AssetResolver,
    EmbeddingSet,
    MultimodalEmbeddingProfile,
    MultimodalEncoder,
    MultimodalImageQueryEncoder,
    MultimodalSourceUnit,
    ProjectionIntegrityError,
    _normalise_embedding_set,
)


class EmbeddingServiceError(RuntimeError):
    """Base error for remote embedding calls."""


class EmbeddingServiceUnavailable(EmbeddingServiceError):
    """A transient network or service availability failure."""


class EmbeddingProtocolError(EmbeddingServiceError):
    """A non-retryable contract, authentication, or profile failure."""


@dataclass(frozen=True, slots=True)
class EmbeddingServiceSettings:
    """Transport limits and authentication for one embedding service."""

    url: str
    token: str | None = None
    timeout_seconds: float = 30.0
    max_request_bytes: int = 5_000_000
    expected_profile_fingerprint: str | None = None
    allowed_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("embedding service URL must use HTTP(S)")
        if self.timeout_seconds <= 0 or self.max_request_bytes <= 0:
            raise ValueError("embedding service limits must be positive")
        hostname = (urlparse(self.url).hostname or "").lower()
        if self.allowed_hosts and hostname not in {host.lower() for host in self.allowed_hosts}:
            raise ValueError(f"embedding service host {hostname!r} is not allowlisted")


def _asset_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, Path):
        raise ProjectionIntegrityError("remote multimodal encoding cannot receive a path asset")
    read = getattr(value, "read", None)
    if callable(read):
        data = read()
        if isinstance(data, bytes):
            return data
    raise ProjectionIntegrityError(
        "remote multimodal encoding requires an asset resolver that returns bytes"
    )


def _item_payload(unit: MultimodalSourceUnit, resolver: AssetResolver | None) -> dict[str, object]:
    item: dict[str, object] = {"item_id": unit.view_id, "modality": unit.modality}
    if unit.text:
        item["text"] = unit.text
    if unit.content_ref is not None:
        if resolver is None:
            raise ProjectionIntegrityError(
                f"asset resolver is required for remote unit {unit.view_id!r}"
            )
        raw = _asset_bytes(resolver.resolve(unit))
        digest = sha256(raw).hexdigest()
        if unit.asset_sha256 and unit.asset_sha256.lower() != digest:
            raise ProjectionIntegrityError(
                f"asset hash mismatch for {unit.view_id!r}: expected {unit.asset_sha256}, got {digest}"
            )
        item["asset"] = {
            "content_type": str(
                unit.metadata.get(
                    "content_type",
                    "image/png" if unit.modality in {"image", "screenshot", "chart"} else "application/octet-stream",
                )
            ),
            "sha256": digest,
            "data_base64": base64.b64encode(raw).decode("ascii"),
        }
    if "text" not in item and "asset" not in item:
        raise ProjectionIntegrityError(f"remote unit {unit.view_id!r} has no content")
    return item


class RemoteMultimodalEncoder(MultimodalEncoder, MultimodalImageQueryEncoder):
    """Use the external service as a drop-in multimodal encoder."""

    contract_version = "v1"

    def __init__(
        self,
        profile: MultimodalEmbeddingProfile,
        settings: EmbeddingServiceSettings,
        *,
        opener: Any = urlopen,
    ) -> None:
        self._profile = profile
        self.settings = settings
        self._opener = opener
        if settings.expected_profile_fingerprint and (
            settings.expected_profile_fingerprint != profile.fingerprint
        ):
            raise ValueError("configured embedding profile fingerprint does not match the encoder profile")

    @property
    def profile(self) -> MultimodalEmbeddingProfile:
        return self._profile

    def readiness(self) -> dict[str, object]:
        """Return a bounded health snapshot for app status reporting."""
        request = Request(self.settings.url.rstrip("/") + "/readyz", method="GET")
        try:
            with self._opener(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read()
                status = int(getattr(response, "status", 200))
        except (HTTPError, TimeoutError, URLError, OSError) as exc:
            return {"ready": False, "reason": str(exc)}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ready": False, "reason": "invalid readiness response"}
        if status >= 400 or not isinstance(payload, Mapping):
            return {"ready": False, "reason": f"HTTP {status}"}
        returned = payload.get("profile")
        if isinstance(returned, Mapping) and returned.get("fingerprint") not in {
            None,
            self.profile.fingerprint,
        }:
            return {"ready": False, "reason": "readiness profile fingerprint mismatch"}
        return {"ready": bool(payload.get("ready")), "profile_fingerprint": self.profile.fingerprint}

    def _request(self, operation: str, items: Sequence[Mapping[str, object]]) -> list[EmbeddingSet]:
        payload = {
            "contract_version": self.contract_version,
            "request_id": sha256(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()[:24],
            "operation": operation,
            "profile_fingerprint": self.profile.fingerprint,
            "items": list(items),
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(body) > self.settings.max_request_bytes:
            raise ProjectionIntegrityError("embedding request exceeds configured byte limit")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.token:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        request = Request(
            self.settings.url.rstrip("/") + "/v1/represent",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.settings.timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
                raw = response.read()
        except HTTPError as exc:
            detail = _http_error_detail(exc)
            message = f"embedding service HTTP {exc.code}{detail}"
            if exc.code in {408, 425, 429} or exc.code >= 500:
                raise EmbeddingServiceUnavailable(message) from exc
            raise EmbeddingProtocolError(message) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise EmbeddingServiceUnavailable("embedding service is unavailable") from exc
        if status >= 500:
            raise EmbeddingServiceUnavailable(f"embedding service HTTP {status}")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EmbeddingProtocolError("embedding service returned invalid JSON") from exc
        if not isinstance(result, Mapping):
            raise EmbeddingProtocolError("embedding response must be an object")
        if result.get("contract_version") != self.contract_version:
            raise EmbeddingProtocolError("embedding contract version mismatch")
        profile_payload = result.get("profile")
        if not isinstance(profile_payload, Mapping):
            raise EmbeddingProtocolError("embedding response omitted profile")
        try:
            returned_profile = MultimodalEmbeddingProfile.from_payload(profile_payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingProtocolError("embedding response contains an invalid profile") from exc
        if returned_profile.fingerprint != self.profile.fingerprint:
            raise EmbeddingProtocolError("embedding profile fingerprint mismatch")
        raw_results = result.get("results")
        if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
            raise EmbeddingProtocolError("embedding response results must be a sequence")
        expected_ids = [str(item["item_id"]) for item in items]
        if len(raw_results) != len(expected_ids):
            raise EmbeddingProtocolError("embedding response count does not match request")
        vectors: list[EmbeddingSet] = []
        for expected_id, raw_item in zip(expected_ids, raw_results):
            if not isinstance(raw_item, Mapping) or raw_item.get("item_id") != expected_id:
                raise EmbeddingProtocolError("embedding response changed item ordering or identity")
            try:
                value = raw_item["vectors"]
                vectors.append(_normalise_embedding_set(value, dimension=self.profile.dimension))
            except (KeyError, TypeError, ValueError, ProjectionIntegrityError) as exc:
                raise EmbeddingProtocolError(
                    f"invalid embedding vector for item {expected_id!r}"
                ) from exc
            if any(not math.isfinite(number) for vector in vectors[-1] for number in vector):
                raise EmbeddingProtocolError("embedding response contains a non-finite value")
        return vectors
    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        del batch_size
        return self._request(
            "query",
            [{"item_id": f"query-{index}", "modality": "text", "text": str(value)} for index, value in enumerate(queries)],
        )

    def encode_documents(
        self,
        units: Sequence[MultimodalSourceUnit],
        *,
        batch_size: int | None = None,
        resolver: AssetResolver | None = None,
    ) -> Sequence[EmbeddingSet]:
        del batch_size
        return self._request("document", [_item_payload(unit, resolver) for unit in units])

    def encode_image_queries(self, images: Sequence[object], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        del batch_size
        items = []
        for index, image in enumerate(images):
            raw = _asset_bytes(image)
            items.append(
                {
                    "item_id": f"image-query-{index}",
                    "modality": "image",
                    "asset": {
                        "content_type": "image/png",
                        "sha256": sha256(raw).hexdigest(),
                        "data_base64": base64.b64encode(raw).decode("ascii"),
                    },
                }
            )
        return self._request("query", items)


def _http_error_detail(error: HTTPError) -> str:
    """Expose only a small server-supplied reason for actionable client errors."""
    try:
        raw = error.read(512)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("error"), str):
        return ""
    return f": {payload['error'][:256]}"


__all__ = [
    "RemoteMultimodalEncoder",
    "EmbeddingProtocolError",
    "EmbeddingServiceError",
    "EmbeddingServiceSettings",
    "EmbeddingServiceUnavailable",
]
