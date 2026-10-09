"""Dependency-light v1 contract shared by the app and embedding service."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, TypeAliasType

EmbeddingKind = Literal["single_vector", "dense", "late_interaction"]
SimilarityMetric = Literal["dot", "cosine"]
SourceModality = Literal[
    "text",
    "image",
    "audio",
    "video",
    "pdf_page",
    "table",
    "chart",
    "webpage",
    "video_frame",
]
EmbeddingSet = tuple[tuple[float, ...], ...]
JsonScalar = TypeAliasType("JsonScalar", None | bool | int | float | str)
JsonValue = TypeAliasType(
    "JsonValue",
    JsonScalar | list["JsonValue"] | dict[str, "JsonValue"],
)
JsonObject = dict[str, JsonValue]


class ContractValidationError(ValueError):
    """A v1 wire or embedding payload is invalid."""


def _payload_int(value: object, *, field: str) -> int:
    """Convert a JSON scalar to an integer at the untyped payload boundary."""

    if not isinstance(value, (str, int, float)):
        raise TypeError(f"{field} must be an integer-compatible scalar")
    return int(value)


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    provider: str
    model: str
    embedding: EmbeddingKind
    dimension: int
    metric: SimilarityMetric = "dot"
    model_revision: str | None = None
    preprocessing_fingerprint: str = "default"
    max_sequence_length: int = 32768
    max_image_patches: int = 768
    crop_token_budget: int | None = None
    tokenizer_fingerprint: str | None = None
    crop_policy: str | None = None

    def __post_init__(self) -> None:
        if not str(self.provider).strip() or not str(self.model).strip():
            raise ContractValidationError("embedding provider and model are required")
        if self.embedding not in {"single_vector", "dense", "late_interaction"}:
            raise ContractValidationError(f"unsupported embedding kind {self.embedding!r}")
        if self.dimension <= 0:
            raise ContractValidationError("embedding dimension must be positive")
        if self.metric not in {"dot", "cosine"}:
            raise ContractValidationError(f"unsupported similarity metric {self.metric!r}")
        if self.max_sequence_length <= 0 or self.max_image_patches <= 0:
            raise ContractValidationError("embedding sequence and patch limits must be positive")
        if self.crop_token_budget is not None and not 0 < self.crop_token_budget <= self.max_sequence_length:
            raise ContractValidationError(
                "embedding crop_token_budget must be positive and no greater than max_sequence_length"
            )

    def canonical_payload(self) -> JsonObject:
        payload = {
            "provider": str(self.provider),
            "model": str(self.model),
            "embedding": self.embedding,
            "dimension": int(self.dimension),
            "metric": self.metric,
            "model_revision": self.model_revision,
            "preprocessing_fingerprint": str(self.preprocessing_fingerprint),
            "max_sequence_length": int(self.max_sequence_length),
            "max_image_patches": int(self.max_image_patches),
        }
        optional = {
            "crop_token_budget": self.crop_token_budget,
            "tokenizer_fingerprint": self.tokenizer_fingerprint,
            "crop_policy": self.crop_policy,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return payload

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return sha256(encoded).hexdigest()

    @classmethod
    def from_payload(cls, payload: Mapping[str, JsonValue]) -> EmbeddingProfile:
        try:
            return cls(
                provider=str(payload["provider"]),
                model=str(payload["model"]),
                embedding=str(payload["embedding"]),  # type: ignore[arg-type]
                dimension=_payload_int(payload["dimension"], field="dimension"),
                metric=str(payload.get("metric", "dot")),  # type: ignore[arg-type]
                model_revision=str(payload["model_revision"]) if payload.get("model_revision") else None,
                preprocessing_fingerprint=str(payload.get("preprocessing_fingerprint", "default")),
                max_sequence_length=_payload_int(
                    payload.get("max_sequence_length", 32768),
                    field="max_sequence_length",
                ),
                max_image_patches=_payload_int(
                    payload.get("max_image_patches", 768),
                    field="max_image_patches",
                ),
                crop_token_budget=(
                    _payload_int(payload["crop_token_budget"], field="crop_token_budget")
                    if payload.get("crop_token_budget") is not None
                    else None
                ),
                tokenizer_fingerprint=(
                    str(payload["tokenizer_fingerprint"])
                    if payload.get("tokenizer_fingerprint") is not None
                    else None
                ),
                crop_policy=(str(payload["crop_policy"]) if payload.get("crop_policy") is not None else None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("invalid embedding profile") from exc


def validate_asset_bytes(data: bytes, *, content_type: str, expected_sha256: str) -> bytes:
    if not isinstance(data, bytes) or not data:
        raise ContractValidationError("asset must contain non-empty bytes")
    if not (content_type.startswith(("image/", "video/")) or content_type == "application/pdf"):
        raise ContractValidationError("asset content type is not supported")
    actual = sha256(data).hexdigest()
    if expected_sha256.lower() != actual:
        raise ContractValidationError("asset hash mismatch")
    return data


def validate_dense_vectors(value: object, *, dimension: int) -> EmbeddingSet:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ContractValidationError("vectors must be a non-empty sequence")
    result: list[tuple[float, ...]] = []
    for raw_vector in value:
        if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, (str, bytes)):
            raise ContractValidationError("each vector must be a sequence")
        vector = tuple(float(number) for number in raw_vector)
        if len(vector) != dimension:
            raise ContractValidationError(f"vector dimension {len(vector)} does not match profile dimension {dimension}")
        if any(not math.isfinite(number) for number in vector):
            raise ContractValidationError("vector contains a non-finite value")
        result.append(vector)
    return tuple(result)


__all__ = [
    "ContractValidationError",
    "EmbeddingKind",
    "EmbeddingProfile",
    "EmbeddingSet",
    "JsonObject",
    "JsonValue",
    "SimilarityMetric",
    "SourceModality",
    "validate_asset_bytes",
    "validate_dense_vectors",
]
