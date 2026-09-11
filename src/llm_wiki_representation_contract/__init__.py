"""Dependency-light v1 contract shared by the app and representation service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Literal

EmbeddingRepresentation = Literal["single_vector", "dense", "late_interaction"]
SimilarityMetric = Literal["dot", "cosine"]
SourceModality = Literal["text", "image", "pdf_page", "table", "chart", "webpage", "video_frame"]
EmbeddingSet = tuple[tuple[float, ...], ...]


class ContractValidationError(ValueError):
    """A v1 wire or representation payload is invalid."""


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    provider: str
    model: str
    representation: EmbeddingRepresentation
    dimension: int
    metric: SimilarityMetric = "dot"
    model_revision: str | None = None
    preprocessing_fingerprint: str = "default"
    max_sequence_length: int = 32768
    max_image_patches: int = 768

    def __post_init__(self) -> None:
        if not str(self.provider).strip() or not str(self.model).strip():
            raise ContractValidationError("embedding provider and model are required")
        if self.representation not in {"single_vector", "dense", "late_interaction"}:
            raise ContractValidationError(f"unsupported embedding representation {self.representation!r}")
        if self.dimension <= 0:
            raise ContractValidationError("embedding dimension must be positive")
        if self.metric not in {"dot", "cosine"}:
            raise ContractValidationError(f"unsupported similarity metric {self.metric!r}")
        if self.max_sequence_length <= 0 or self.max_image_patches <= 0:
            raise ContractValidationError("embedding sequence and patch limits must be positive")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "provider": str(self.provider),
            "model": str(self.model),
            "representation": self.representation,
            "dimension": int(self.dimension),
            "metric": self.metric,
            "model_revision": self.model_revision,
            "preprocessing_fingerprint": str(self.preprocessing_fingerprint),
            "max_sequence_length": int(self.max_sequence_length),
            "max_image_patches": int(self.max_image_patches),
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return sha256(encoded).hexdigest()

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "EmbeddingProfile":
        try:
            return cls(
                provider=str(payload["provider"]),
                model=str(payload["model"]),
                representation=str(payload["representation"]),  # type: ignore[arg-type]
                dimension=int(payload["dimension"]),
                metric=str(payload.get("metric", "dot")),  # type: ignore[arg-type]
                model_revision=str(payload["model_revision"]) if payload.get("model_revision") else None,
                preprocessing_fingerprint=str(payload.get("preprocessing_fingerprint", "default")),
                max_sequence_length=int(payload.get("max_sequence_length", 32768)),
                max_image_patches=int(payload.get("max_image_patches", 768)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("invalid embedding profile") from exc


def validate_asset_bytes(data: bytes, *, content_type: str, expected_sha256: str) -> bytes:
    if not isinstance(data, bytes) or not data:
        raise ContractValidationError("asset must contain non-empty bytes")
    if not (content_type.startswith("image/") or content_type.startswith("video/") or content_type == "application/pdf"):
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
    "EmbeddingProfile", "EmbeddingRepresentation", "EmbeddingSet", "SimilarityMetric",
    "SourceModality", "ContractValidationError", "validate_asset_bytes", "validate_dense_vectors",
]
