"""Application-owned multimodal capture and projection.

The normal Kogwistar node and edge embeddings remain single-vector fields.
This module stores multimodal retrieval views separately, which lets a
ColQwen-style encoders can return one vector per token or image patch without
changing the graph schema.

Qwen3-VL is represented separately as a normalized dense vector per source
unit.  The embedding is part of the profile fingerprint, so equal
dimensions never make late-interaction and dense spaces interchangeable.

Stage 1 stores only source-unit metadata and references. Stage 2 adds the
embedding set after the source reference has been captured and validated.
SQLite is deliberately a small portable reference store. PostgreSQL graph
projections use profile-derived schemas, while Chroma uses profile-derived
collections and sidecars; no physical vector table or collection mixes
profiles.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from hashlib import sha256
from io import BytesIO
from math import sqrt
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from kogwistar.engine_core import (
    EmbeddingProfile as CoreEmbeddingProfile,
)
from kogwistar.engine_core import (
    EmbeddingReference,
    LegacyLocator,
    MultimodalSpan,
    SpatialRegionLocator,
    TemporalIntervalLocator,
    TextRangeLocator,
    VideoRegionTrackLocator,
)
from kogwistar.json_types import JsonObject
from kogwistar.typing_interfaces import (
    SqlAlchemyConnectionLike,
    SqlAlchemyEngineLike,
)

from llm_wiki_embedding_contract import (
    EmbeddingProfile as MultimodalEmbeddingProfile,
)
from llm_wiki_embedding_contract import (
    EmbeddingSet,
    SimilarityMetric,
    SourceModality,
)

from .multimodal_runtime import (
    configured_multimodal_backend,
    configured_multimodal_dimension,
    configured_multimodal_model,
    configured_multimodal_revision,
    configured_torch_backend,
    validate_torch_runtime,
)

DEFAULT_COLQWEN_MODEL = "vidore/colqwen2-v1.0-hf"
DEFAULT_COLQWEN_REVISION = "ddc07d2317c80f75fc742b7362ee9ad1912908f9"
DEFAULT_QWEN3_VL_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
QWEN3_VL_MIN_DIMENSION = 64
QWEN3_VL_MAX_DIMENSION = 2048


def _as_int(value: object, *, field_name: str) -> int:
    """Narrow a legacy payload scalar before constructing a typed locator."""

    if isinstance(value, bool):
        raise ProjectionIntegrityError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ProjectionIntegrityError(
                f"{field_name} must be an integer"
            ) from exc
    raise ProjectionIntegrityError(f"{field_name} must be an integer")


def _as_float(value: object, *, field_name: str) -> float:
    """Narrow a legacy payload scalar before constructing a typed locator."""

    if isinstance(value, bool):
        raise ProjectionIntegrityError(f"{field_name} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise ProjectionIntegrityError(
                f"{field_name} must be a number"
            ) from exc
    raise ProjectionIntegrityError(f"{field_name} must be a number")


def _optional_int(payload: Mapping[str, object], field_name: str) -> int | None:
    value = payload.get(field_name)
    return None if value is None or value == "" else _as_int(value, field_name=field_name)


def _mapping_value(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ProjectionIntegrityError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


def _message_content(message: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = message.get("content", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(part for part in raw if isinstance(part, Mapping))


def _projection_storage_key(workspace_id: str, view_id: str) -> str:
    """Keep public view IDs stable while making physical IDs workspace-safe."""

    return "mmv2-" + sha256(f"{workspace_id}\x00{view_id}".encode()).hexdigest()


class EmbeddingProfileMismatch(ValueError):
    """Raised before a projection can mix incompatible embedding profiles."""


class ProjectionIntegrityError(ValueError):
    """Raised when a stage transition or vector payload is invalid."""


def _move_to_device(value: object, device: str) -> object:
    mover = getattr(value, "to", None)
    return mover(device) if callable(mover) else value


@dataclass(frozen=True, slots=True)
class MultimodalSourceUnit:
    """A revision-bound retrieval unit, never the source bytes themselves."""

    view_id: str
    workspace_id: str
    source_id: str
    source_revision_id: str
    modality: SourceModality
    locator: Mapping[str, object]
    content_ref: str | None = None
    text: str | None = None
    asset_sha256: str | None = None
    source_namespace: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    embedding_reference: EmbeddingReference | None = None

    def __post_init__(self) -> None:
        if not self.view_id or not self.workspace_id or not self.source_id or not self.source_revision_id:
            raise ValueError("multimodal source units require stable identity fields")
        if self.modality not in {
            "text", "image", "audio", "video", "pdf_page", "table", "chart", "webpage", "video_frame"
        }:
            raise ValueError(f"unsupported source modality {self.modality!r}")
        if not self.content_ref and not self.text:
            raise ValueError("a source unit requires content_ref or text")
        if self.source_namespace is not None and not self.source_namespace.strip():
            raise ValueError("source_namespace cannot be empty")

    def to_payload(self) -> dict[str, object]:
        return {
            "view_id": self.view_id,
            "workspace_id": self.workspace_id,
            "source_id": self.source_id,
            "source_revision_id": self.source_revision_id,
            "source_namespace": self.source_namespace,
            "modality": self.modality,
            "locator": dict(self.locator),
            "content_ref": self.content_ref,
            "text": self.text,
            "asset_sha256": self.asset_sha256,
            "metadata": dict(self.metadata),
            "embedding_reference": (
                self.embedding_reference.model_dump(mode="json")
                if self.embedding_reference is not None
                else None
            ),
        }

    def to_multimodal_span(self) -> MultimodalSpan:
        """Convert a legacy source-unit locator into the core evidence contract."""

        if (
            self.content_ref is not None
            and self.modality != "text"
            and not re.fullmatch(r"[0-9a-fA-F]{64}", self.asset_sha256 or "")
        ):
            raise ProjectionIntegrityError(
                "asset-backed multimodal spans require the SHA-256 digest of "
                "the resolved source bytes"
            )

        locator = dict(self.locator)
        kind = str(locator.get("kind", "legacy"))
        if kind in {"legacy", "text_span", "text_range"} and "start_char" in locator and "end_char" in locator:
            typed_locator = TextRangeLocator(
                start_char=_as_int(locator["start_char"], field_name="start_char"),
                end_char=_as_int(locator["end_char"], field_name="end_char"),
                page_number=_optional_int(locator, "page_number"),
            )
        elif kind in {"whole_image", "image_region", "dom_image"}:
            typed_locator = SpatialRegionLocator(
                x=_as_float(locator.get("x", 0.0), field_name="x"),
                y=_as_float(locator.get("y", 0.0), field_name="y"),
                width=_as_float(locator.get("width", 1.0), field_name="width"),
                height=_as_float(locator.get("height", 1.0), field_name="height"),
                coordinate_system=str(locator.get("coordinate_system", "normalized_0_1")),  # type: ignore[arg-type]
                page_number=_optional_int(locator, "page_number"),
                frame_index=_optional_int(locator, "frame_index"),
                timestamp_ms=_optional_int(locator, "timestamp_ms"),
            )
        elif kind in {"audio_interval", "video_interval", "temporal_interval"}:
            typed_locator = TemporalIntervalLocator(
                start_ms=_as_int(locator["start_ms"], field_name="start_ms"),
                end_ms=_as_int(locator["end_ms"], field_name="end_ms"),
            )
        elif kind == "video_region_track":
            typed_locator = VideoRegionTrackLocator(
                start_ms=_as_int(locator["start_ms"], field_name="start_ms"),
                end_ms=_as_int(locator["end_ms"], field_name="end_ms"),
                track_manifest_ref=str(locator["track_manifest_ref"]),
                track_manifest_sha256=str(locator["track_manifest_sha256"]),
                manifest_schema_version=_as_int(
                    locator.get("manifest_schema_version", 1),
                    field_name="manifest_schema_version",
                ),
            )
        elif self.modality in {"text", "webpage", "pdf_page", "table"} and self.text:
            # Historical text/table units often carried only a semantic locator
            # label. Their immutable source text still gives us a safe bounded
            # whole-unit range for new indexing.
            typed_locator = TextRangeLocator(
                start_char=0,
                end_char=len(self.text),
                page_number=_optional_int(locator, "page_number"),
            )
        else:
            typed_locator = LegacyLocator(payload=cast(JsonObject, locator))

        import hashlib

        source_digest = self.asset_sha256 or hashlib.sha256(
            (self.text or self.content_ref or self.view_id).encode("utf-8")
        ).hexdigest()
        modality = "video" if self.modality == "video_frame" else self.modality
        return MultimodalSpan(
            source_namespace=self.source_namespace or self.workspace_id,
            resource_id=self.source_id,
            resource_revision_id=self.source_revision_id,
            content_sha256=source_digest,
            modality=modality,  # type: ignore[arg-type]
            locator=typed_locator,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> MultimodalSourceUnit:
        return cls(
            view_id=str(payload["view_id"]),
            workspace_id=str(payload["workspace_id"]),
            source_id=str(payload["source_id"]),
            source_revision_id=str(payload["source_revision_id"]),
            source_namespace=(
                str(payload["source_namespace"])
                if payload.get("source_namespace")
                else None
            ),
            modality=str(payload["modality"]),  # type: ignore[arg-type]
            locator=_mapping_value(payload.get("locator") or {}, field_name="locator"),
            content_ref=str(payload["content_ref"]) if payload.get("content_ref") else None,
            text=str(payload["text"]) if payload.get("text") else None,
            asset_sha256=str(payload["asset_sha256"]) if payload.get("asset_sha256") else None,
            metadata=_mapping_value(payload.get("metadata") or {}, field_name="metadata"),
            embedding_reference=(
                EmbeddingReference.model_validate(payload["embedding_reference"])
                if payload.get("embedding_reference")
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class MultimodalSearchHit:
    view_id: str
    score: float
    source_id: str
    source_revision_id: str
    modality: str
    locator: dict[str, object]
    metadata: dict[str, object]
    multimodal_span: MultimodalSpan | None = None
    embedding_reference_id: str | None = None
    dereference_status: str = "unresolved"


def to_core_embedding_profile(profile: MultimodalEmbeddingProfile) -> CoreEmbeddingProfile:
    """Adapt the dependency-light wire profile to the core semantic-space profile."""

    return CoreEmbeddingProfile(
        provider=profile.provider,
        model=profile.model,
        dimension=profile.dimension,
        similarity_metric="ip" if profile.metric == "dot" else profile.metric,
        max_sequence_length=profile.max_sequence_length,
        crop_token_budget=profile.crop_token_budget,
        tokenizer_fingerprint=profile.tokenizer_fingerprint,
        crop_policy=profile.crop_policy,
        embedding_kind=profile.embedding,
        model_revision=profile.model_revision,
        preprocessing_fingerprint=profile.preprocessing_fingerprint,
        max_image_patches=profile.max_image_patches,
    )


@runtime_checkable
class MultimodalEncoder(Protocol):
    @property
    def profile(self) -> MultimodalEmbeddingProfile: ...

    def readiness(self) -> Mapping[str, object]: ...

    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]: ...

    def encode_documents(
        self,
        units: Sequence[MultimodalSourceUnit],
        *,
        batch_size: int | None = None,
        resolver: AssetResolver | None = None,
    ) -> Sequence[EmbeddingSet]: ...


@runtime_checkable
class MultimodalImageQueryEncoder(Protocol):
    """Optional image-query capability for native cross-modal retrieval."""

    @property
    def profile(self) -> MultimodalEmbeddingProfile: ...

    def encode_image_queries(
        self, images: Sequence[object], *, batch_size: int | None = None
    ) -> Sequence[EmbeddingSet]: ...


@runtime_checkable
class AssetResolver(Protocol):
    def resolve(self, unit: MultimodalSourceUnit) -> object: ...


class _NativeModelLike(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...

    def eval(self) -> _NativeModelLike: ...


class _NativeProcessorLike(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


class _TensorMetadataLike(Protocol):
    ndim: int


NativeVisionProcessor = Callable[..., object]


@runtime_checkable
class MultimodalProjectionStore(Protocol):
    @property
    def profile(self) -> MultimodalEmbeddingProfile: ...

    @property
    def projection_scope(self) -> str: ...

    def capture(self, unit: MultimodalSourceUnit) -> None: ...

    def capture_many(self, units: Sequence[MultimodalSourceUnit]) -> None: ...

    def pending_units(
        self, *, workspace_id: str | None = None
    ) -> Sequence[MultimodalSourceUnit]: ...

    def stage_counts(self, *, workspace_id: str | None = None) -> dict[str, int]: ...

    def get(
        self,
        view_id: str,
        *,
        profile: MultimodalEmbeddingProfile,
        workspace_id: str | None = None,
    ) -> MultimodalSourceUnit | None: ...

    def upsert_embedding(
        self,
        unit: MultimodalSourceUnit,
        vectors: object,
        *,
        profile: MultimodalEmbeddingProfile,
    ) -> None: ...

    def search(
        self,
        query_vectors: object,
        *,
        profile: MultimodalEmbeddingProfile,
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[MultimodalSearchHit]: ...

    def close(self) -> None: ...


def _normalise_embedding_set(value: object, *, dimension: int) -> EmbeddingSet:
    """Convert nested provider output to an immutable, validated embedding set."""

    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()  # type: ignore[union-attr]
    elif hasattr(value, "tolist"):
        value = value.tolist()  # type: ignore[union-attr]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProjectionIntegrityError("embedding output must be a sequence of vectors")
    vectors: list[tuple[float, ...]] = []
    for vector in value:
        if hasattr(vector, "detach"):
            vector = vector.detach().cpu().tolist()  # type: ignore[union-attr]
        elif hasattr(vector, "tolist"):
            vector = vector.tolist()  # type: ignore[union-attr]
        if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)):
            raise ProjectionIntegrityError("embedding output contains a non-vector item")
        item = tuple(float(component) for component in vector)
        if len(item) != dimension:
            raise ProjectionIntegrityError(
                f"embedding dimension mismatch: expected {dimension}, received {len(item)}"
            )
        vectors.append(item)
    if not vectors:
        raise ProjectionIntegrityError("embedding output must contain at least one vector")
    return tuple(vectors)


def _normalise_sets(values: Sequence[object], *, profile: MultimodalEmbeddingProfile) -> list[EmbeddingSet]:
    if hasattr(values, "detach"):
        values = values.detach().cpu().tolist()  # type: ignore[union-attr]
    result = [_normalise_embedding_set(value, dimension=profile.dimension) for value in values]
    if profile.embedding in {"single_vector", "dense"} and any(len(item) != 1 for item in result):
        raise ProjectionIntegrityError(
            f"{profile.embedding} profiles require exactly one vector per view"
        )
    return result


def _vector_score(left: Sequence[float], right: Sequence[float], metric: SimilarityMetric) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    if metric == "dot":
        return dot
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def score_embedding_sets(
    query: EmbeddingSet,
    document: EmbeddingSet,
    *,
    profile: MultimodalEmbeddingProfile,
) -> float:
    """Score pooled vectors or ColBERT-style late interaction sets."""

    if len(query) != 1 and profile.embedding in {"single_vector", "dense"}:
        raise ProjectionIntegrityError(f"{profile.embedding} query must contain one vector")
    if profile.embedding in {"single_vector", "dense"}:
        return _vector_score(query[0], document[0], profile.metric)
    # ColBERT/ColQwen MaxSim: each query token chooses its best document token.
    return sum(
        max(_vector_score(query_vector, document_vector, profile.metric) for document_vector in document)
        for query_vector in query
    )


def _validate_captured_unit(
    unit: MultimodalSourceUnit,
    *,
    profile: MultimodalEmbeddingProfile,
    require_asset_digest: bool = False,
) -> None:
    """Apply the common stage-1 evidence/profile checks for every backend."""

    binary_modalities = {
        "image", "audio", "video", "pdf_page", "table", "chart", "video_frame"
    }
    if (
        require_asset_digest
        and unit.content_ref is not None
        and unit.modality in binary_modalities
        and not re.fullmatch(r"[0-9a-fA-F]{64}", unit.asset_sha256 or "")
    ):
        raise ProjectionIntegrityError(
            "stage-2 indexing of asset-backed multimodal units requires "
            "the SHA-256 digest of the resolved asset"
        )

    if unit.embedding_reference is not None:
        if unit.embedding_reference.profile_fingerprint != profile.fingerprint:
            raise EmbeddingProfileMismatch(
                "embedding reference profile does not match projection profile"
            )
        if unit.embedding_reference.span.evidence_key != unit.to_multimodal_span().evidence_key:
            raise ProjectionIntegrityError("embedding reference evidence does not match source unit")
        if unit.embedding_reference.source_namespace != (
            unit.source_namespace or unit.workspace_id
        ):
            raise ProjectionIntegrityError(
                "embedding reference namespace does not match source unit"
            )


def _is_digest_only_upgrade(
    existing: MultimodalSourceUnit,
    replacement: MultimodalSourceUnit,
) -> bool:
    """Allow Stage 1 to gain its verified asset digest during promotion."""

    if existing.asset_sha256 is not None or replacement.asset_sha256 is None:
        return False
    old_payload = existing.to_payload()
    new_payload = replacement.to_payload()
    old_payload.pop("asset_sha256", None)
    new_payload.pop("asset_sha256", None)
    return old_payload == new_payload and bool(
        re.fullmatch(r"[0-9a-fA-F]{64}", replacement.asset_sha256)
    )


class InMemoryMultimodalProjectionStore:
    """Deterministic store used by tests and small local demonstrations."""

    def __init__(self, *, scope: str, profile: MultimodalEmbeddingProfile) -> None:
        self.scope = str(scope)
        self.profile = profile
        self._units: dict[str, MultimodalSourceUnit] = {}
        self._embeddings: dict[str, EmbeddingSet] = {}

    @property
    def projection_scope(self) -> str:
        """Physical projection identity, including the complete semantic profile."""

        return f"{self.scope}:profile:{self.profile.fingerprint}"

    def _check_profile(self, profile: MultimodalEmbeddingProfile) -> None:
        if profile.fingerprint != self.profile.fingerprint:
            raise EmbeddingProfileMismatch(
                f"multimodal projection {self.scope!r} is bound to {self.profile.fingerprint}, "
                f"not {profile.fingerprint}"
            )

    def capture(self, unit: MultimodalSourceUnit) -> None:
        self.capture_many((unit,))

    def capture_many(self, units: Sequence[MultimodalSourceUnit]) -> None:
        pending = tuple(units)
        seen: set[str] = set()
        replacements: list[tuple[str, MultimodalSourceUnit]] = []
        for unit in pending:
            _validate_captured_unit(unit, profile=self.profile)
            storage_key = _projection_storage_key(unit.workspace_id, unit.view_id)
            if storage_key in seen:
                raise ProjectionIntegrityError(
                    f"duplicate source view {unit.view_id!r} in capture batch"
                )
            seen.add(storage_key)
            existing = self._units.get(storage_key)
            if existing is not None and (
                existing.to_payload() != unit.to_payload()
                and not _is_digest_only_upgrade(existing, unit)
            ):
                raise ProjectionIntegrityError(
                    f"source view {unit.view_id!r} was changed in place"
                )
            replacements.append((storage_key, unit))
        for storage_key, unit in replacements:
            self._units[storage_key] = unit

    def upsert_embedding(self, unit: MultimodalSourceUnit, vectors: object, *, profile: MultimodalEmbeddingProfile) -> None:
        self._check_profile(profile)
        _validate_captured_unit(unit, profile=self.profile, require_asset_digest=True)
        if isinstance(unit.to_multimodal_span().locator, LegacyLocator):
            raise ProjectionIntegrityError(
                "legacy multimodal locators are readable but must be converted before indexing"
            )
        normalised = _normalise_embedding_set(vectors, dimension=profile.dimension)
        if profile.embedding in {"single_vector", "dense"} and len(normalised) != 1:
            raise ProjectionIntegrityError(
                f"{profile.embedding} profiles require exactly one vector per view"
            )
        self.capture(unit)
        self._embeddings[_projection_storage_key(unit.workspace_id, unit.view_id)] = normalised

    def stage_counts(self, *, workspace_id: str | None = None) -> dict[str, int]:
        units = tuple(
            unit
            for unit in self._units.values()
            if workspace_id is None or unit.workspace_id == workspace_id
        )
        keys = {
            storage_key
            for storage_key, unit in self._units.items()
            if workspace_id is None or unit.workspace_id == workspace_id
        }
        embedded = sum(1 for storage_key in self._embeddings if storage_key in keys)
        return {
            "stage1": len(units),
            "stage2": embedded,
            "pending_stage2": len(units) - embedded,
        }

    def pending_units(
        self, *, workspace_id: str | None = None
    ) -> Sequence[MultimodalSourceUnit]:
        return tuple(
            unit
            for storage_key, unit in self._units.items()
            if storage_key not in self._embeddings
            and (workspace_id is None or unit.workspace_id == workspace_id)
        )

    def get(
        self,
        view_id: str,
        *,
        profile: MultimodalEmbeddingProfile,
        workspace_id: str | None = None,
    ) -> MultimodalSourceUnit | None:
        self._check_profile(profile)
        matches = tuple(
            unit
            for unit in self._units.values()
            if unit.view_id == str(view_id)
            and (workspace_id is None or unit.workspace_id == workspace_id)
        )
        return matches[0] if len(matches) == 1 else None

    def close(self) -> None:
        """Release resources; the in-memory adapter has none to release."""



    def search(
        self,
        query_vectors: object,
        *,
        profile: MultimodalEmbeddingProfile,
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[MultimodalSearchHit]:
        self._check_profile(profile)
        query = _normalise_embedding_set(query_vectors, dimension=profile.dimension)
        scored = [
            (score_embedding_sets(query, vectors, profile=profile), self._units[storage_key])
            for storage_key, vectors in self._embeddings.items()
            if workspace_id is None or self._units[storage_key].workspace_id == workspace_id
        ]
        scored.sort(key=lambda item: (-item[0], item[1].view_id))
        return [
            MultimodalSearchHit(
                view_id=unit.view_id,
                score=score,
                source_id=unit.source_id,
                source_revision_id=unit.source_revision_id,
                modality=unit.modality,
                locator=dict(unit.locator),
                metadata=dict(unit.metadata),
                multimodal_span=unit.to_multimodal_span(),
                embedding_reference_id=(
                    unit.embedding_reference.reference_id
                    if unit.embedding_reference is not None
                    else None
                ),
                dereference_status="unresolved",
            )
            for score, unit in scored[: max(0, int(limit))]
        ]


class SQLiteMultimodalProjectionStore(InMemoryMultimodalProjectionStore):
    """Persistent reference implementation for Stage 1 and late interaction."""

    def __init__(self, path: str | Path, *, scope: str, profile: MultimodalEmbeddingProfile) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS multimodal_projection_profile (
                scope TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                profile_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS multimodal_source_unit (
                view_id TEXT PRIMARY KEY,
                unit_json TEXT NOT NULL,
                stage TEXT NOT NULL CHECK (stage IN ('stage1', 'stage2')),
                embedding_json TEXT
            );
            """
        )
        row = self._connection.execute(
            "SELECT fingerprint, profile_json FROM multimodal_projection_profile WHERE scope = ?", (str(scope),)
        ).fetchone()
        if row is not None and str(row["fingerprint"]) != profile.fingerprint:
            existing = MultimodalEmbeddingProfile.from_payload(json.loads(str(row["profile_json"])))
            self._connection.close()
            raise EmbeddingProfileMismatch(
                f"multimodal projection {scope!r} is bound to profile {existing.fingerprint}, "
                f"not {profile.fingerprint}"
            )
        if row is None:
            self._connection.execute(
                "INSERT INTO multimodal_projection_profile(scope, fingerprint, profile_json) VALUES (?, ?, ?)",
                (str(scope), profile.fingerprint, json.dumps(profile.canonical_payload(), sort_keys=True)),
            )
            self._connection.commit()
        super().__init__(scope=scope, profile=profile)
        self._load_rows()

    def _load_rows(self) -> None:
        for row in self._connection.execute("SELECT view_id, unit_json, embedding_json FROM multimodal_source_unit"):
            unit = MultimodalSourceUnit.from_payload(json.loads(str(row["unit_json"])))
            storage_key = _projection_storage_key(unit.workspace_id, unit.view_id)
            self._units[storage_key] = unit
            if row["embedding_json"]:
                self._embeddings[storage_key] = _normalise_embedding_set(
                    json.loads(str(row["embedding_json"])), dimension=self.profile.dimension
                )
            if str(row["view_id"]) != storage_key:
                self._connection.execute(
                    "UPDATE multimodal_source_unit SET view_id = ? WHERE view_id = ?",
                    (storage_key, str(row["view_id"])),
                )
        self._connection.commit()

    def capture(self, unit: MultimodalSourceUnit) -> None:
        self.capture_many((unit,))

    def capture_many(self, units: Sequence[MultimodalSourceUnit]) -> None:
        pending = tuple(units)
        seen: set[str] = set()
        rows: list[tuple[str, str]] = []
        for unit in pending:
            _validate_captured_unit(unit, profile=self.profile)
            storage_key = _projection_storage_key(unit.workspace_id, unit.view_id)
            if storage_key in seen:
                raise ProjectionIntegrityError(
                    f"duplicate source view {unit.view_id!r} in capture batch"
                )
            seen.add(storage_key)
            existing = self._connection.execute(
                "SELECT unit_json FROM multimodal_source_unit WHERE view_id = ?",
                (storage_key,),
            ).fetchone()
            if existing is not None:
                existing_unit = MultimodalSourceUnit.from_payload(
                    json.loads(str(existing["unit_json"]))
                )
                if (
                    existing_unit.to_payload() != unit.to_payload()
                    and not _is_digest_only_upgrade(existing_unit, unit)
                ):
                    raise ProjectionIntegrityError(
                        f"source view {unit.view_id!r} was changed in place"
                    )
            rows.append((storage_key, json.dumps(unit.to_payload(), sort_keys=True)))
        with self._connection:
            for storage_key, payload in rows:
                self._connection.execute(
                    """
                    INSERT INTO multimodal_source_unit(view_id, unit_json, stage, embedding_json)
                    VALUES (?, ?, 'stage1', NULL)
                    ON CONFLICT(view_id) DO UPDATE SET unit_json = excluded.unit_json
                    """,
                    (storage_key, payload),
                )
        for storage_key, unit in zip(
            (_projection_storage_key(unit.workspace_id, unit.view_id) for unit in pending),
            pending,
        ):
            self._units[storage_key] = unit

    def upsert_embedding(self, unit: MultimodalSourceUnit, vectors: object, *, profile: MultimodalEmbeddingProfile) -> None:
        self._check_profile(profile)
        _validate_captured_unit(unit, profile=self.profile, require_asset_digest=True)
        if isinstance(unit.to_multimodal_span().locator, LegacyLocator):
            raise ProjectionIntegrityError(
                "legacy multimodal locators are readable but must be converted before indexing"
            )
        normalised = _normalise_embedding_set(vectors, dimension=profile.dimension)
        if profile.embedding in {"single_vector", "dense"} and len(normalised) != 1:
            raise ProjectionIntegrityError(
                f"{profile.embedding} profiles require exactly one vector per view"
            )
        self.capture(unit)
        storage_key = _projection_storage_key(unit.workspace_id, unit.view_id)
        self._embeddings[storage_key] = normalised
        self._connection.execute(
            "UPDATE multimodal_source_unit SET stage = 'stage2', embedding_json = ? WHERE view_id = ?",
            (json.dumps(normalised), storage_key),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


class ChromaMultimodalProjectionStore(SQLiteMultimodalProjectionStore):
    """Persistent Chroma projection with exact application-side MaxSim.

    Stage-1 metadata and the authoritative profile binding live in a SQLite
    sidecar next to the Chroma directory. Chroma stores one row per vector in a
    late-interaction set; query scoring groups those rows by ``view_id`` and
    computes the same bounded operator as the in-memory reference store.
    """

    def __init__(
        self,
        persist_directory: str | Path,
        *,
        scope: str,
        profile: MultimodalEmbeddingProfile,
        collection_name: str | None = None,
        max_search_vectors: int = 100_000,
    ) -> None:
        try:
            import chromadb  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise RuntimeError(
                "Chroma multimodal projection requires the chromadb extra"
            ) from exc
        self.persist_directory = Path(persist_directory).expanduser().resolve()
        if max_search_vectors <= 0:
            raise ValueError("max_search_vectors must be positive")
        self.max_search_vectors = int(max_search_vectors)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        # Chroma collections are profile-scoped, so the durable sidecar must
        # be profile-scoped too.  A shared sidecar keyed only by ``scope``
        # would make a second dimension collide on the stable view_id primary
        # key even though its Chroma collection is isolated.
        profile_key = sha256(profile.fingerprint.encode("utf-8")).hexdigest()[:32]
        state_path = self.persist_directory / f".kogwistar-multimodal-state-{profile_key}.sqlite3"
        legacy_state_path = self.persist_directory / ".kogwistar-multimodal-state.sqlite3"
        if not state_path.exists() and legacy_state_path.exists() and _legacy_sidecar_matches(
            legacy_state_path, scope=scope, fingerprint=profile.fingerprint
        ):
            # Existing installations used one sidecar before profile isolation
            # was enforced. Reuse it only when its binding is this profile;
            # otherwise a new profile-scoped sidecar is required.
            state_path = legacy_state_path
        super().__init__(state_path, scope=scope, profile=profile)
        self._client = chromadb.PersistentClient(path=str(self.persist_directory))
        collection_basis = collection_name or self.scope
        name = f"mm_{sha256(f'{collection_basis}:profile:{self.profile.fingerprint}'.encode()).hexdigest()[:24]}"
        self._collection = self._client.get_or_create_collection(name=name)
        self._validate_physical_rows()

    def _expected_vector_count(self) -> int:
        return sum(len(vectors) for vectors in self._embeddings.values())

    def _validate_physical_rows(self) -> None:
        expected = self._expected_vector_count()
        actual = int(self._collection.count())
        if actual != expected:
            raise ProjectionIntegrityError(
                f"Chroma multimodal projection row mismatch for {self.scope!r}: "
                f"state expects {expected} vectors, physical collection has {actual}"
            )

    def capture(self, unit: MultimodalSourceUnit) -> None:
        SQLiteMultimodalProjectionStore.capture(self, unit)

    def upsert_embedding(
        self,
        unit: MultimodalSourceUnit,
        vectors: object,
        *,
        profile: MultimodalEmbeddingProfile,
    ) -> None:
        self._check_profile(profile)
        _validate_captured_unit(unit, profile=self.profile, require_asset_digest=True)
        normalised = _normalise_embedding_set(vectors, dimension=profile.dimension)
        if profile.embedding in {"single_vector", "dense"} and len(normalised) != 1:
            raise ProjectionIntegrityError(
                f"{profile.embedding} profiles require exactly one vector per view"
            )
        storage_key = _projection_storage_key(unit.workspace_id, unit.view_id)
        ids = [f"{storage_key}:{ordinal}" for ordinal in range(len(normalised))]
        metadatas: list[dict[str, str | int | float | bool | None]] = [
            {
                "view_id": unit.view_id,
                "storage_key": storage_key,
                "workspace_id": unit.workspace_id,
                "vector_ordinal": ordinal,
                "profile_fingerprint": profile.fingerprint,
            }
            for ordinal in range(len(normalised))
        ]
        # Physical vectors are written first. If the sidecar commit is
        # interrupted, the unit remains Stage 1 and this idempotent upsert can
        # be retried without promoting an incomplete state.
        self._collection.upsert(
            ids=ids,
            embeddings=[list(vector) for vector in normalised],
            metadatas=cast(Any, metadatas),
        )
        SQLiteMultimodalProjectionStore.upsert_embedding(
            self, unit, normalised, profile=profile
        )

    def search(
        self,
        query_vectors: object,
        *,
        profile: MultimodalEmbeddingProfile,
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[MultimodalSearchHit]:
        self._check_profile(profile)
        query = _normalise_embedding_set(query_vectors, dimension=profile.dimension)
        if int(self._collection.count()) > self.max_search_vectors:
            raise ProjectionIntegrityError(
                f"Chroma exact MaxSim scan exceeds configured bound of "
                f"{self.max_search_vectors} vectors"
            )
        rows = self._collection.get(include=["embeddings", "metadatas"])
        grouped: dict[str, list[tuple[int, object]]] = {}
        raw_embeddings = rows.get("embeddings")
        raw_metadatas = rows.get("metadatas")
        embeddings = [] if raw_embeddings is None else raw_embeddings
        metadatas = [] if raw_metadatas is None else raw_metadatas
        for vector, metadata in zip(embeddings, metadatas):
            if not isinstance(metadata, Mapping):
                raise ProjectionIntegrityError("Chroma multimodal metadata is not a mapping")
            if str(metadata.get("profile_fingerprint")) != profile.fingerprint:
                raise EmbeddingProfileMismatch("Chroma multimodal row has an incompatible profile")
            storage_key = str(metadata.get("storage_key") or "")
            if not storage_key:
                # Read legacy rows written before workspace-safe physical IDs.
                public_view_id = str(metadata.get("view_id") or "")
                matches = [
                    key for key, unit in self._units.items() if unit.view_id == public_view_id
                ]
                if len(matches) != 1:
                    raise ProjectionIntegrityError(
                        f"Chroma contains an ambiguous legacy source view {public_view_id!r}"
                    )
                storage_key = matches[0]
            if storage_key not in self._units:
                raise ProjectionIntegrityError(
                    f"Chroma contains an unknown source view {storage_key!r}"
                )
            raw_ordinal = metadata.get("vector_ordinal", 0)
            if not isinstance(raw_ordinal, (int, float, str)):
                raise ProjectionIntegrityError("Chroma vector ordinal is not numeric")
            grouped.setdefault(storage_key, []).append((int(raw_ordinal), vector))
        scored: list[tuple[float, MultimodalSourceUnit]] = []
        for storage_key, values in grouped.items():
            values.sort(key=lambda item: item[0])
            vectors = _normalise_embedding_set(
                [value for _, value in values], dimension=profile.dimension
            )
            unit = self._units[storage_key]
            if workspace_id is not None and unit.workspace_id != workspace_id:
                continue
            scored.append((score_embedding_sets(query, vectors, profile=profile), unit))
        scored.sort(key=lambda item: (-item[0], item[1].view_id))
        return [
            MultimodalSearchHit(
                view_id=unit.view_id,
                score=score,
                source_id=unit.source_id,
                source_revision_id=unit.source_revision_id,
                modality=unit.modality,
                locator=dict(unit.locator),
                metadata=dict(unit.metadata),
                multimodal_span=unit.to_multimodal_span(),
                embedding_reference_id=(
                    unit.embedding_reference.reference_id
                    if unit.embedding_reference is not None
                    else None
                ),
                dereference_status="unresolved",
            )
            for score, unit in scored[: max(0, int(limit))]
        ]

    def close(self) -> None:
        SQLiteMultimodalProjectionStore.close(self)


class PgVectorMultimodalProjectionStore:
    """Profile-isolated PostgreSQL/pgvector projection store.

    This adapter deliberately owns separate projection tables and never uses
    Kogwistar's canonical node or edge tables.  The table names include the
    complete profile fingerprint, so a single PostgreSQL database can safely
    host dimensions, models, preprocessing policies, and late-interaction
    sets that are not semantically interchangeable.

    Search uses the same exact bounded Python scoring operator as the memory
    and Chroma adapters.  It is a correctness-first reference implementation;
    an ANN shortlist can be added later without changing the contract.
    """

    _IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def __init__(
        self,
        dsn: str | None = None,
        *,
        engine: SqlAlchemyEngineLike | None = None,
        scope: str,
        profile: MultimodalEmbeddingProfile,
        schema: str = "public",
        max_search_vectors: int = 100_000,
    ) -> None:
        try:
            import sqlalchemy as sa
            from pgvector.sqlalchemy import (  # pyright: ignore[reportMissingImports]
                Vector,
            )
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL multimodal projection requires sqlalchemy and pgvector"
            ) from exc
        if engine is None and not dsn:
            raise ValueError("provide either a PostgreSQL dsn or an existing SQLAlchemy engine")
        if max_search_vectors <= 0:
            raise ValueError("max_search_vectors must be positive")
        if not self._IDENTIFIER.fullmatch(str(schema)):
            raise ValueError("PostgreSQL schema must be a simple SQL identifier")

        self.scope = str(scope)
        self.profile = profile
        self.schema = str(schema)
        self.max_search_vectors = int(max_search_vectors)
        self._owns_engine = engine is None
        self._engine: SqlAlchemyEngineLike = (
            engine if engine is not None else sa.create_engine(str(dsn))
        )
        self._metadata = sa.MetaData(schema=self.schema)
        profile_key = sha256(profile.fingerprint.encode("utf-8")).hexdigest()[:32]
        self._profile_key = profile_key
        self._profile_table = sa.Table(
            f"kogwistar_mm_profile_{profile_key}",
            self._metadata,
            sa.Column("scope", sa.Text, primary_key=True),
            sa.Column("fingerprint", sa.Text, nullable=False),
            sa.Column("profile_json", sa.Text, nullable=False),
        )
        self._unit_table = sa.Table(
            f"kogwistar_mm_unit_{profile_key}",
            self._metadata,
            sa.Column("view_id", sa.Text, primary_key=True),
            sa.Column("unit_json", sa.Text, nullable=False),
            sa.Column("stage", sa.Text, nullable=False),
        )
        self._vector_table = sa.Table(
            f"kogwistar_mm_vector_{profile_key}",
            self._metadata,
            sa.Column("vector_id", sa.Text, primary_key=True),
            sa.Column("view_id", sa.Text, nullable=False, index=True),
            sa.Column("vector_ordinal", sa.Integer, nullable=False),
            sa.Column("embedding", Vector(profile.dimension), nullable=False),
            sa.UniqueConstraint("view_id", "vector_ordinal"),
        )
        self._ensure_schema()
        self._ensure_vector_extension()
        create_all = cast(Callable[[object], None], self._metadata.create_all)
        create_all(self._engine)
        self._bind_profile()

    def _ensure_schema(self) -> None:
        if self.schema == "public":
            return
        with self._engine.begin() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')

    def _ensure_vector_extension(self) -> None:
        """Make the pgvector type available before creating profile tables."""

        with self._engine.begin() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")

    def _bind_profile(self) -> None:
        import sqlalchemy as sqlalchemy_module

        with self._engine.begin() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            row = connection.execute(
                sqlalchemy_module.select(self._profile_table).where(
                    self._profile_table.c.scope == self.scope
                )
            ).mappings().first()
            if row is not None and str(row["fingerprint"]) != self.profile.fingerprint:
                raise EmbeddingProfileMismatch(
                    f"multimodal projection {self.scope!r} is bound to "
                    f"{row['fingerprint']}, not {self.profile.fingerprint}"
                )
            if row is None:
                connection.execute(
                    self._profile_table.insert().values(
                        scope=self.scope,
                        fingerprint=self.profile.fingerprint,
                        profile_json=json.dumps(self.profile.canonical_payload(), sort_keys=True),
                    )
                )

    @property
    def projection_scope(self) -> str:
        return f"{self.schema}:{self.scope}:profile:{self.profile.fingerprint}"

    def _check_profile(self, profile: MultimodalEmbeddingProfile) -> None:
        if profile.fingerprint != self.profile.fingerprint:
            raise EmbeddingProfileMismatch(
                f"multimodal projection {self.scope!r} is bound to {self.profile.fingerprint}, "
                f"not {profile.fingerprint}"
            )

    def capture(self, unit: MultimodalSourceUnit) -> None:
        self.capture_many((unit,))

    def capture_many(self, units: Sequence[MultimodalSourceUnit]) -> None:
        import sqlalchemy as sa

        pending = tuple(units)
        seen: set[str] = set()
        with self._engine.begin() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            rows: list[tuple[str, str]] = []
            for unit in pending:
                _validate_captured_unit(unit, profile=self.profile)
                storage_key = _projection_storage_key(unit.workspace_id, unit.view_id)
                if storage_key in seen:
                    raise ProjectionIntegrityError(
                        f"duplicate source view {unit.view_id!r} in capture batch"
                    )
                seen.add(storage_key)
                existing = connection.execute(
                    sa.select(self._unit_table.c.unit_json).where(
                        self._unit_table.c.view_id == storage_key
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing_unit = MultimodalSourceUnit.from_payload(json.loads(str(existing)))
                    if (
                        existing_unit.to_payload() != unit.to_payload()
                        and not _is_digest_only_upgrade(existing_unit, unit)
                    ):
                        raise ProjectionIntegrityError(
                            f"source view {unit.view_id!r} was changed in place"
                        )
                rows.append((storage_key, json.dumps(unit.to_payload(), sort_keys=True)))
            for storage_key, payload in rows:
                if connection.execute(
                    sa.select(self._unit_table.c.view_id).where(
                        self._unit_table.c.view_id == storage_key
                    )
                ).first() is None:
                    connection.execute(
                        self._unit_table.insert().values(
                            view_id=storage_key,
                            unit_json=payload,
                            stage="stage1",
                        )
                    )
                else:
                    connection.execute(
                        self._unit_table.update()
                        .where(self._unit_table.c.view_id == storage_key)
                        .values(unit_json=payload)
                    )

    def upsert_embedding(
        self,
        unit: MultimodalSourceUnit,
        vectors: object,
        *,
        profile: MultimodalEmbeddingProfile,
    ) -> None:
        import sqlalchemy as sa

        self._check_profile(profile)
        _validate_captured_unit(unit, profile=self.profile, require_asset_digest=True)
        if isinstance(unit.to_multimodal_span().locator, LegacyLocator):
            raise ProjectionIntegrityError(
                "legacy multimodal locators are readable but must be converted before indexing"
            )
        normalised = _normalise_embedding_set(vectors, dimension=profile.dimension)
        if profile.embedding in {"single_vector", "dense"} and len(normalised) != 1:
            raise ProjectionIntegrityError(
                f"{profile.embedding} profiles require exactly one vector per view"
            )
        _validate_captured_unit(unit, profile=self.profile)
        storage_key = _projection_storage_key(unit.workspace_id, unit.view_id)
        payload = json.dumps(unit.to_payload(), sort_keys=True)
        with self._engine.begin() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            existing = connection.execute(
                sa.select(self._unit_table.c.unit_json).where(
                    self._unit_table.c.view_id == storage_key
                )
            ).scalar_one_or_none()
            if existing is not None and json.loads(str(existing)) != unit.to_payload():
                raise ProjectionIntegrityError(f"source view {unit.view_id!r} was changed in place")
            if existing is None:
                connection.execute(
                    self._unit_table.insert().values(
                        view_id=storage_key,
                        unit_json=payload,
                        stage="stage1",
                    )
                )
            else:
                connection.execute(
                    self._unit_table.update()
                    .where(self._unit_table.c.view_id == storage_key)
                    .values(unit_json=payload)
                )
            connection.execute(
                self._vector_table.delete().where(self._vector_table.c.view_id == storage_key)
            )
            connection.execute(
                self._vector_table.insert(),
                [
                    {
                        "vector_id": f"{storage_key}:{ordinal}",
                        "view_id": storage_key,
                        "vector_ordinal": ordinal,
                        "embedding": list(vector),
                    }
                    for ordinal, vector in enumerate(normalised)
                ],
            )
            connection.execute(
                self._unit_table.update()
                .where(self._unit_table.c.view_id == storage_key)
                .values(stage="stage2")
            )

    def stage_counts(self, *, workspace_id: str | None = None) -> dict[str, int]:
        import sqlalchemy as sa

        with self._engine.connect() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            unit_payloads = connection.execute(
                sa.select(self._unit_table.c.unit_json)
            ).scalars().all()
            units = tuple(
                MultimodalSourceUnit.from_payload(json.loads(str(payload)))
                for payload in unit_payloads
            )
            if workspace_id is not None:
                units = tuple(unit for unit in units if unit.workspace_id == workspace_id)
            stage1 = len(units)
            stage2 = int(
                cast(
                    int,
                    connection.execute(
                    sa.select(sa.func.count(sa.distinct(self._vector_table.c.view_id)))
                    .select_from(self._vector_table)
                    ).scalar_one(),
                )
            )
            if workspace_id is not None:
                stage2 = sum(
                    1
                    for unit in units
                    if connection.execute(
                        sa.select(sa.func.count())
                        .select_from(self._vector_table)
                        .where(self._vector_table.c.view_id == _projection_storage_key(unit.workspace_id, unit.view_id))
                    ).scalar_one()
                )
        return {"stage1": stage1, "stage2": stage2, "pending_stage2": stage1 - stage2}

    def _load_unit_by_storage_key(
        self, connection: SqlAlchemyConnectionLike, storage_key: str
    ) -> MultimodalSourceUnit | None:
        sa = __import__("sqlalchemy")
        row = connection.execute(
            sa.select(self._unit_table.c.unit_json).where(
                self._unit_table.c.view_id == storage_key
            )
        ).scalar_one_or_none()
        return MultimodalSourceUnit.from_payload(json.loads(str(row))) if row is not None else None

    def get(
        self,
        view_id: str,
        *,
        profile: MultimodalEmbeddingProfile,
        workspace_id: str | None = None,
    ) -> MultimodalSourceUnit | None:
        import sqlalchemy as sa

        self._check_profile(profile)
        with self._engine.connect() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            if workspace_id is not None:
                unit = self._load_unit_by_storage_key(
                    cast(SqlAlchemyConnectionLike, connection),
                    _projection_storage_key(workspace_id, view_id),
                )
            else:
                rows = connection.execute(sa.select(self._unit_table.c.unit_json)).scalars().all()
                matches = []
                for row in rows:
                    candidate = MultimodalSourceUnit.from_payload(json.loads(str(row)))
                    if candidate.view_id == str(view_id):
                        matches.append(candidate)
                unit = matches[0] if len(matches) == 1 else None
            return unit if unit is not None and (
                workspace_id is None or unit.workspace_id == workspace_id
            ) else None

    def pending_units(
        self, *, workspace_id: str | None = None
    ) -> Sequence[MultimodalSourceUnit]:
        import sqlalchemy as sa

        with self._engine.connect() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            unit_payloads = connection.execute(
                sa.select(self._unit_table.c.unit_json)
                .select_from(
                    self._unit_table.outerjoin(
                        self._vector_table,
                        self._unit_table.c.view_id == self._vector_table.c.view_id,
                    )
                )
                .where(self._vector_table.c.view_id.is_(None))
                .distinct()
            ).scalars().all()
            units = tuple(
                MultimodalSourceUnit.from_payload(json.loads(str(payload)))
                for payload in unit_payloads
            )
            return tuple(
                unit for unit in units
                if workspace_id is None or unit.workspace_id == workspace_id
            )

    def search(
        self,
        query_vectors: object,
        *,
        profile: MultimodalEmbeddingProfile,
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[MultimodalSearchHit]:
        import sqlalchemy as sa

        self._check_profile(profile)
        query = _normalise_embedding_set(query_vectors, dimension=profile.dimension)
        with self._engine.connect() as raw_connection:
            connection = cast(SqlAlchemyConnectionLike, raw_connection)
            count = int(
                cast(
                    int,
                    connection.execute(
                        sa.select(sa.func.count()).select_from(self._vector_table)
                    ).scalar_one(),
                )
            )
            if count > self.max_search_vectors:
                raise ProjectionIntegrityError(
                    f"PostgreSQL exact multimodal scan exceeds configured bound of "
                    f"{self.max_search_vectors} vectors"
                )
            rows = connection.execute(
                sa.select(
                    self._vector_table.c.view_id,
                    self._vector_table.c.vector_ordinal,
                    self._vector_table.c.embedding,
                ).order_by(self._vector_table.c.view_id, self._vector_table.c.vector_ordinal)
            )
            grouped: dict[str, list[object]] = {}
            for row in rows:
                mapping = cast(Mapping[str, object], getattr(row, "_mapping", {}))
                grouped.setdefault(str(mapping["view_id"]), []).append(mapping["embedding"])
            scored: list[tuple[float, MultimodalSourceUnit]] = []
            for view_id, values in grouped.items():
                unit = self._load_unit_by_storage_key(
                    cast(SqlAlchemyConnectionLike, connection), view_id
                )
                if unit is None:
                    raise ProjectionIntegrityError(f"vector row references unknown source view {view_id!r}")
                if workspace_id is not None and unit.workspace_id != workspace_id:
                    continue
                vectors = _normalise_embedding_set(values, dimension=profile.dimension)
                scored.append((score_embedding_sets(query, vectors, profile=profile), unit))
        scored.sort(key=lambda item: (-item[0], item[1].view_id))
        return [
            MultimodalSearchHit(
                view_id=unit.view_id,
                score=score,
                source_id=unit.source_id,
                source_revision_id=unit.source_revision_id,
                modality=unit.modality,
                locator=dict(unit.locator),
                metadata=dict(unit.metadata),
                multimodal_span=unit.to_multimodal_span(),
                embedding_reference_id=(
                    unit.embedding_reference.reference_id
                    if unit.embedding_reference is not None
                    else None
                ),
                dereference_status="unresolved",
            )
            for score, unit in scored[: max(0, int(limit))]
        ]

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()


def _legacy_sidecar_matches(path: Path, *, scope: str, fingerprint: str) -> bool:
    """Check an old Chroma sidecar without opening it through the new store."""

    rows: list[tuple[object, ...]] = []
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        rows = connection.execute(
            "SELECT fingerprint FROM multimodal_projection_profile WHERE scope = ?",
            (str(scope),),
        ).fetchall()
    except sqlite3.Error:
        return False
    finally:
        try:
            if connection is not None:
                connection.close()
        except (NameError, sqlite3.Error):
            pass
    return not rows or all(str(row[0]) == fingerprint for row in rows)


@dataclass(frozen=True, slots=True)
class FakeMultimodalEncoder:
    """Provider-free late-interaction encoder with stable test vectors."""

    profile: MultimodalEmbeddingProfile = field(
        default_factory=lambda: MultimodalEmbeddingProfile(
            provider="fake", model="fake-colqwen-compatible", embedding="late_interaction", dimension=8
        )
    )

    def readiness(self) -> dict[str, object]:
        """Return the deterministic provider-free encoder's readiness state."""

        return {"ready": True, "provider": self.profile.provider}

    def _vector(self, value: str, ordinal: int) -> tuple[float, ...]:
        digest = sha256(f"{value}\x00{ordinal}".encode()).digest()
        return tuple((digest[index] / 127.5) - 1.0 for index in range(self.profile.dimension))

    def _encode(self, values: Sequence[str]) -> list[EmbeddingSet]:
        return [
            tuple(self._vector(token, index) for index, token in enumerate(str(value).split()[:16])) or (self._vector("", 0),)
            for value in values
        ]

    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        del batch_size
        return self._encode(queries)

    def encode_image_queries(
        self, images: Sequence[object], *, batch_size: int | None = None
    ) -> Sequence[EmbeddingSet]:
        del batch_size
        return self._encode([str(image) for image in images])

    def encode_documents(
        self,
        units: Sequence[MultimodalSourceUnit],
        *,
        batch_size: int | None = None,
        resolver: AssetResolver | None = None,
    ) -> Sequence[EmbeddingSet]:
        del batch_size, resolver
        return self._encode([unit.text or unit.content_ref or unit.view_id for unit in units])


class ColQwenNativeEncoder:
    """Optional native Transformers adapter for a 4-bit ColQwen2 checkpoint.

    Imports are lazy so the normal text-only package and provider-free tests do
    not require torch, transformers, Pillow, or bitsandbytes.
    """

    def __init__(
        self,
        model: _NativeModelLike,
        processor: _NativeProcessorLike,
        *,
        profile: MultimodalEmbeddingProfile,
        device: str,
        batch_size: int = 1,
    ) -> None:
        self._model = model
        self._processor = processor
        self._profile = profile
        self.device = device
        self.batch_size = max(1, int(batch_size))

    @property
    def profile(self) -> MultimodalEmbeddingProfile:
        return self._profile

    def readiness(self) -> dict[str, object]:
        """Report readiness without forcing an additional model probe."""

        return {"ready": self._model is not None, "provider": self._profile.provider}

    @classmethod
    def from_pretrained(
        cls,
        model_id: str = DEFAULT_COLQWEN_MODEL,
        *,
        revision: str | None = None,
        device: str | None = None,
        load_in_4bit: bool = True,
        batch_size: int = 1,
        dimension: int | None = None,
        max_sequence_length: int = 32768,
        max_image_patches: int = 768,
    ) -> ColQwenNativeEncoder:
        try:
            import torch
            from transformers import AutoProcessor, ColQwen2ForRetrieval
        except ImportError as exc:
            raise RuntimeError(
                "ColQwen requires the optional multimodal dependencies in the "
                f"active interpreter ({sys.executable}); install them with "
                f'"{sys.executable}" -m pip install -e ".[multimodal-cpu]" '
                "(or install a CUDA requirements profile with .[multimodal-cuda])"
            ) from exc

        selected_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        configured_backend = configured_torch_backend()
        if configured_backend != "none":
            validate_torch_runtime(configured_backend, require_device=selected_device == "cuda")
        elif selected_device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "ColQwen was configured for CUDA but this Torch runtime has no usable GPU. "
                "Install requirements/multimodal/torch-cu126.txt or torch-cu128.txt "
                "with the .[multimodal-cuda] extra, or use device='cpu'."
            )
        if selected_device == "cuda":
            try:
                import accelerate  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "CUDA ColQwen loading requires accelerate because Transformers uses "
                    "device_map='auto'. Install the complete CUDA runtime with "
                    f'"{sys.executable}" -m pip install -e ".[multimodal-cuda]".'
                ) from exc
        effective_revision = (
            revision
            or DEFAULT_COLQWEN_REVISION
            if model_id == DEFAULT_COLQWEN_MODEL
            else revision
        )
        model_kwargs: dict[str, object] = {"revision": effective_revision} if effective_revision else {}
        if selected_device == "cuda" and load_in_4bit:
            try:
                from transformers import BitsAndBytesConfig
                model_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.float16,
                    # ColQwen reads this head's weight dtype before projecting.
                    # Quantizing it exposes packed uint8 storage and makes the
                    # model cast hidden states to bytes before L2 normalization.
                    llm_int8_skip_modules=["embedding_proj_layer"],
                )
                model_kwargs["device_map"] = "auto"
            except ImportError as exc:
                raise RuntimeError(
                    "4-bit ColQwen loading requires bitsandbytes; set load_in_4bit=False "
                    "or install the multimodal CUDA dependencies"
                ) from exc
        elif selected_device == "cuda":
            model_kwargs.update({"torch_dtype": torch.float16, "device_map": "auto"})
        else:
            model_kwargs["torch_dtype"] = torch.float32

        load_model = cast(Callable[..., _NativeModelLike], ColQwen2ForRetrieval.from_pretrained)
        model = load_model(model_id, **model_kwargs).eval()
        load_processor = cast(Callable[..., _NativeProcessorLike], AutoProcessor.from_pretrained)
        processor = (
            load_processor(model_id, revision=effective_revision)
            if effective_revision
            else load_processor(model_id)
        )
        resolved_dimension = int(
            dimension or getattr(getattr(model, "config", None), "embedding_dim", 128)
        )
        profile = MultimodalEmbeddingProfile(
            provider="transformers",
            model=model_id,
            model_revision=effective_revision,
            embedding="late_interaction",
            dimension=resolved_dimension,
            metric="dot",
            preprocessing_fingerprint=f"colqwen2:{max_image_patches}:{max_sequence_length}",
            max_sequence_length=max_sequence_length,
            max_image_patches=max_image_patches,
        )
        return cls(model, processor, profile=profile, device=selected_device, batch_size=batch_size)

    def _run(self, inputs: object) -> Sequence[EmbeddingSet]:
        import torch

        if not isinstance(inputs, Mapping):
            raise ProjectionIntegrityError("ColQwen processor must return a mapping")
        model_inputs = {
            str(key): _move_to_device(value, self.device)
            for key, value in inputs.items()
        }
        attention_mask = model_inputs.get("attention_mask")
        with torch.inference_mode():
            output = self._model(**model_inputs)
        embeddings = getattr(output, "embeddings", output)
        if hasattr(embeddings, "detach"):
            embeddings = cast(torch.Tensor, embeddings).detach().cpu()
        if attention_mask is not None and hasattr(attention_mask, "detach"):
            masks = cast(torch.Tensor, attention_mask).detach().cpu()
            embeddings = [
                row[mask.to(dtype=torch.bool)]
                for row, mask in zip(
                    cast(Sequence[torch.Tensor], embeddings),
                    cast(Sequence[torch.Tensor], masks),
                )
            ]
        return _normalise_sets(cast(Sequence[object], embeddings), profile=self.profile)

    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        return self._encode_text_values(queries, batch_size=batch_size)

    def _encode_text_values(
        self, values: Sequence[str], *, batch_size: int | None = None
    ) -> list[EmbeddingSet]:
        process_queries = self._processor.__call__
        chunk_size = max(1, int(batch_size or self.batch_size))
        encoded: list[EmbeddingSet] = []
        for start in range(0, len(values), chunk_size):
            batch = process_queries(
                text=list(values[start : start + chunk_size]),
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.profile.max_sequence_length,
            )
            encoded.extend(self._run(batch))
        return encoded

    def encode_documents(
        self,
        units: Sequence[MultimodalSourceUnit],
        *,
        batch_size: int | None = None,
        resolver: AssetResolver | None = None,
    ) -> Sequence[EmbeddingSet]:
        """Encode mixed source views while preserving the caller's order."""

        encoded: list[EmbeddingSet | None] = [None] * len(units)
        start = 0
        while start < len(units):
            unit = units[start]
            text_mode = unit.text is not None
            end = start + 1
            while end < len(units) and (units[end].text is not None) == text_mode:
                end += 1
            group = units[start:end]
            if text_mode:
                vectors = self._encode_text_values(
                    [str(group_unit.text) for group_unit in group], batch_size=batch_size
                )
            else:
                values: list[object] = []
                for group_unit in group:
                    if group_unit.modality not in {"image", "video_frame", "pdf_page", "table", "chart"}:
                        raise ProjectionIntegrityError(
                            f"ColQwen document {group_unit.view_id!r} needs text or a supported image reference"
                        )
                    value = resolver.resolve(group_unit) if resolver is not None else group_unit.content_ref
                    if value is None:
                        raise ProjectionIntegrityError(
                            f"ColQwen document {group_unit.view_id!r} has no image content reference"
                        )
                    values.append(value)
                vectors = self._encode_image_values(values, batch_size=batch_size)
            if len(vectors) != len(group):
                raise ProjectionIntegrityError(
                    f"ColQwen returned {len(vectors)} embeddings for {len(group)} source views"
                )
            encoded[start:end] = vectors
            start = end
        if any(value is None for value in encoded):
            raise ProjectionIntegrityError("ColQwen did not encode every source view")
        return [value for value in encoded if value is not None]

    def encode_image_queries(
        self, images: Sequence[object], *, batch_size: int | None = None
    ) -> Sequence[EmbeddingSet]:
        """Encode image queries directly, without captioning or text conversion."""
        return self._encode_image_values(images, batch_size=batch_size)

    def _encode_image_values(
        self, values: Sequence[object], *, batch_size: int | None = None
    ) -> list[EmbeddingSet]:
        from PIL import Image

        chunk_size = max(1, int(batch_size or self.batch_size))
        encoded: list[EmbeddingSet] = []
        for start in range(0, len(values), chunk_size):
            images: list[object] = []
            owned: list[object] = []
            try:
                for value in values[start : start + chunk_size]:
                    if hasattr(value, "size"):
                        image = value
                    elif isinstance(value, (bytes, bytearray, memoryview)):
                        image = Image.open(BytesIO(bytes(value)))
                    elif isinstance(value, (str, Path)):
                        image = Image.open(value)
                    else:
                        raise ProjectionIntegrityError(
                            "image query must be bytes, a path, or an image object"
                        )
                    images.append(image)
                    if image is not value:
                        owned.append(image)
                batch = self._processor(
                    images=images,
                    return_tensors="pt",
                    max_pixels=28 * 28 * self.profile.max_image_patches,
                )
                encoded.extend(self._run(batch))
            finally:
                for image in owned:
                    close = getattr(image, "close", None)
                    if callable(close):
                        close()
        return encoded


class Qwen3VLDenseEncoder:
    """Native dense adapter for ``Qwen/Qwen3-VL-Embedding-2B``.

    The heavy runtime is imported only by ``from_pretrained``.  Tests and
    applications may inject a model, processor, and vision processor, which
    keeps the base installation Torch-free and makes the batching contract
    provider-free testable.
    """

    def __init__(
        self,
        model: _NativeModelLike,
        processor: _NativeProcessorLike,
        *,
        profile: MultimodalEmbeddingProfile,
        device: str,
        batch_size: int = 1,
        vision_processor: NativeVisionProcessor | None = None,
        instruction: str = "Represent the user's input.",
    ) -> None:
        if profile.embedding != "dense":
            raise ValueError("Qwen3VLDenseEncoder requires a dense embedding profile")
        if not QWEN3_VL_MIN_DIMENSION <= profile.dimension <= QWEN3_VL_MAX_DIMENSION:
            raise ValueError(
                f"Qwen3-VL dimension must be between {QWEN3_VL_MIN_DIMENSION} and "
                f"{QWEN3_VL_MAX_DIMENSION}; got {profile.dimension}"
            )
        self._model = model
        self._processor = processor
        self._vision_processor = vision_processor
        self._profile = profile
        self.device = device
        self.batch_size = max(1, int(batch_size))
        self.instruction = instruction

    @property
    def profile(self) -> MultimodalEmbeddingProfile:
        return self._profile

    def readiness(self) -> dict[str, object]:
        """Report readiness without importing or probing optional runtimes."""

        return {"ready": self._model is not None, "provider": self._profile.provider}

    @classmethod
    def from_pretrained(
        cls,
        model_id: str = DEFAULT_QWEN3_VL_MODEL,
        *,
        revision: str | None = None,
        device: str | None = None,
        batch_size: int = 1,
        dimension: int = 1024,
        max_sequence_length: int = 32768,
        max_image_patches: int = 768,
        instruction: str = "Represent the user's input.",
    ) -> Qwen3VLDenseEncoder:
        if not QWEN3_VL_MIN_DIMENSION <= int(dimension) <= QWEN3_VL_MAX_DIMENSION:
            raise ValueError(
                f"Qwen3-VL dimension must be between {QWEN3_VL_MIN_DIMENSION} and "
                f"{QWEN3_VL_MAX_DIMENSION}; got {dimension}"
            )
        try:
            import torch
            from qwen_vl_utils import (  # pyright: ignore[reportMissingImports]
                process_vision_info,
            )
            from transformers import AutoModelForMultimodalLM, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "Qwen3-VL requires the optional multimodal dependencies in the active "
                f"interpreter ({sys.executable}); install them with "
                f'"{sys.executable}" -m pip install -e ".[multimodal-cpu]" '
                "or use the matching CUDA requirements profile"
            ) from exc

        selected_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        configured_backend = configured_torch_backend()
        if configured_backend != "none":
            validate_torch_runtime(configured_backend, require_device=selected_device == "cuda")
        elif selected_device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "Qwen3-VL was configured for CUDA but no usable GPU is visible; "
                "use device='cpu' or install the matching CUDA runtime profile"
            )
        if selected_device == "cuda":
            try:
                import accelerate  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "CUDA Qwen3-VL loading requires accelerate for device_map='auto'; "
                    f'install "{sys.executable}" -m pip install -e ".[multimodal-cuda]"'
                ) from exc

        model_kwargs: dict[str, object] = {"trust_remote_code": True}
        if revision:
            model_kwargs["revision"] = revision
        if selected_device == "cuda":
            model_kwargs.update({"torch_dtype": torch.float16, "device_map": "auto"})
        else:
            model_kwargs["torch_dtype"] = torch.float32
        load_model = cast(Callable[..., _NativeModelLike], AutoModelForMultimodalLM.from_pretrained)
        model = load_model(model_id, **model_kwargs).eval()
        processor_kwargs: dict[str, object] = {"trust_remote_code": True}
        if revision:
            processor_kwargs["revision"] = revision
        load_processor = cast(Callable[..., _NativeProcessorLike], AutoProcessor.from_pretrained)
        try:
            processor = load_processor(
                model_id, padding_side="right", **processor_kwargs
            )
        except TypeError:
            processor = load_processor(model_id, **processor_kwargs)
        profile = MultimodalEmbeddingProfile(
            provider="transformers",
            model=model_id,
            model_revision=revision,
            embedding="dense",
            dimension=int(dimension),
            metric="dot",
            preprocessing_fingerprint=(
                f"qwen3-vl:dense:{max_image_patches}:{max_sequence_length}:"
                f"{sha256(instruction.encode('utf-8')).hexdigest()[:16]}"
            ),
            max_sequence_length=max_sequence_length,
            max_image_patches=max_image_patches,
        )
        return cls(
            model,
            processor,
            profile=profile,
            device=selected_device,
            batch_size=batch_size,
            vision_processor=process_vision_info,
            instruction=instruction,
        )

    def _conversation(
        self, value: object, *, image: bool = False, text: str | None = None
    ) -> list[dict[str, object]]:
        content: list[dict[str, object]] = []
        if image:
            content.append({"type": "image", "image": value})
        if text is not None:
            content.append({"type": "text", "text": text})
        elif not image:
            content.append({"type": "text", "text": str(value)})
        return [
            {"role": "system", "content": [{"type": "text", "text": self.instruction}]},
            {"role": "user", "content": content},
        ]

    @staticmethod
    def _decode_image_bytes(value: object, owned: list[object]) -> object:
        """Turn transport-safe asset bytes into the image object Qwen expects."""
        if not isinstance(value, (bytes, bytearray, memoryview)):
            return value
        try:
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(bytes(value))) as decoded:
                image = decoded.convert("RGB")
        except Exception as exc:
            raise ProjectionIntegrityError("Qwen3-VL could not decode the supplied image asset") from exc
        owned.append(image)
        return image

    def _prepare(self, conversations: Sequence[list[dict[str, object]]]) -> object:
        apply_template = getattr(self._processor, "apply_chat_template", None)
        if callable(apply_template):
            texts = apply_template(
                conversations, add_generation_prompt=True, tokenize=False
            )
        else:
            texts = [
                " ".join(
                    str(part.get("text", ""))
                    for message in conversation
                    for part in _message_content(message)
                )
                for conversation in conversations
            ]
        images: object = None
        videos: object = None
        video_metadata: object = None
        video_kwargs: dict[str, object] = {}
        if self._vision_processor is not None:
            processed = self._vision_processor(
                conversations,
                image_patch_size=16,
                return_video_metadata=True,
                return_video_kwargs=True,
            )
            if isinstance(processed, tuple) and len(processed) == 4:
                images, videos, video_metadata, video_kwargs = processed
            elif isinstance(processed, tuple) and len(processed) == 3:
                images, videos, third_value = processed
                if isinstance(third_value, Mapping):
                    video_kwargs = dict(third_value)
                else:
                    video_metadata = third_value
            elif isinstance(processed, tuple) and len(processed) == 2:
                images, videos = processed
            else:
                images = processed
        kwargs: dict[str, object] = {
            "text": texts,
            "truncation": True,
            "max_length": self.profile.max_sequence_length,
            "padding": True,
            "return_tensors": "pt",
        }
        if images is not None:
            kwargs["images"] = images
        if videos is not None:
            kwargs["videos"] = videos
        if video_metadata is not None:
            kwargs["video_metadata"] = video_metadata
        kwargs.update(video_kwargs)
        return self._processor(**kwargs)

    def _run(self, inputs: object) -> Sequence[EmbeddingSet]:
        import torch

        if isinstance(inputs, Mapping):
            inputs = {
                key: _move_to_device(value, self.device)
                for key, value in inputs.items()
            }
        with torch.inference_mode():
            # The public Qwen checkpoint is registered as a causal LM. Its
            # logits are vocabulary-sized and are not embeddings; use the
            # loaded multimodal backbone, as the checkpoint reference adapter
            # does, to obtain last hidden states for pooling.
            inference_model = getattr(self._model, "model", self._model)
            output = (
                inference_model(**inputs)
                if isinstance(inputs, Mapping)
                else inference_model(inputs)
            )
        embeddings = getattr(output, "embeddings", None)
        if embeddings is None:
            embeddings = getattr(output, "last_hidden_state", output)
        tensor_metadata = cast(_TensorMetadataLike, embeddings)
        if hasattr(embeddings, "ndim") and int(tensor_metadata.ndim) == 3:
            tensor_embeddings = cast(torch.Tensor, embeddings)
            mask = inputs.get("attention_mask") if isinstance(inputs, Mapping) else None
            if mask is None:
                embeddings = tensor_embeddings[:, -1, :]
            else:
                mask_tensor = cast(torch.Tensor, mask)
                positions = mask_tensor.long().sum(dim=1).clamp_min(1) - 1
                embeddings = tensor_embeddings[
                    torch.arange(tensor_embeddings.shape[0], device=tensor_embeddings.device),
                    positions,
                ]
        if hasattr(embeddings, "ndim"):
            tensor_embeddings = cast(torch.Tensor, embeddings)
        else:
            tensor_embeddings = None
        if tensor_embeddings is not None and tensor_embeddings.ndim != 2:
            raise ProjectionIntegrityError("Qwen3-VL must return one dense vector per source view")
        if tensor_embeddings is not None:
            from torch.nn import functional

            embeddings = functional.normalize(
                tensor_embeddings[..., : self.profile.dimension], p=2, dim=-1
            )
            # Dense profiles expose one vector per view, while the common
            # normalizer receives a set of vectors per view.
            return _normalise_sets([[row] for row in embeddings], profile=self.profile)
        else:
            # This branch also makes the adapter contract testable with a tiny
            # fake model in environments that intentionally omit Torch.
            normalised: list[list[list[float]]] = []
            if not isinstance(embeddings, Sequence) or isinstance(embeddings, (str, bytes)):
                raise ProjectionIntegrityError("Qwen3-VL returned an unsupported embedding payload")
            for row in embeddings:
                if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
                    raise ProjectionIntegrityError("Qwen3-VL returned a non-vector row")
                values = [float(value) for value in row[: self.profile.dimension]]
                norm = sqrt(sum(value * value for value in values))
                if norm == 0.0:
                    raise ProjectionIntegrityError("Qwen3-VL returned a zero dense vector")
                normalised.append([[value / norm for value in values]])
            embeddings = normalised
        return _normalise_sets(embeddings, profile=self.profile)

    def _encode_conversations(
        self, conversations: Sequence[list[dict[str, object]]], *, batch_size: int | None = None
    ) -> list[EmbeddingSet]:
        chunk_size = max(1, int(batch_size or self.batch_size))
        encoded: list[EmbeddingSet] = []
        for start in range(0, len(conversations), chunk_size):
            encoded.extend(self._run(self._prepare(conversations[start : start + chunk_size])))
        return encoded

    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        return self._encode_conversations(
            [self._conversation(query) for query in queries], batch_size=batch_size
        )

    def encode_documents(
        self,
        units: Sequence[MultimodalSourceUnit],
        *,
        batch_size: int | None = None,
        resolver: AssetResolver | None = None,
    ) -> Sequence[EmbeddingSet]:
        conversations: list[list[dict[str, object]]] = []
        owned: list[object] = []
        try:
            for unit in units:
                if unit.content_ref is not None and unit.modality in {
                    "image", "video_frame", "pdf_page", "table", "chart"
                }:
                    value = resolver.resolve(unit) if resolver is not None else unit.content_ref
                    if value is None:
                        raise ProjectionIntegrityError(
                            f"Qwen3-VL document {unit.view_id!r} has no resolvable asset"
                        )
                    conversations.append(
                        self._conversation(
                            self._decode_image_bytes(value, owned), image=True, text=unit.text
                        )
                    )
                elif unit.text:
                    conversations.append(self._conversation(unit.text))
                else:
                    raise ProjectionIntegrityError(
                        f"Qwen3-VL document {unit.view_id!r} has no text or asset"
                    )
            result = self._encode_conversations(conversations, batch_size=batch_size)
            if len(result) != len(units):
                raise ProjectionIntegrityError(
                    f"Qwen3-VL returned {len(result)} embeddings for {len(units)} source views"
                )
            return result
        finally:
            for value in owned:
                close = getattr(value, "close", None)
                if callable(close):
                    close()

    def encode_image_queries(
        self, images: Sequence[object], *, batch_size: int | None = None
    ) -> Sequence[EmbeddingSet]:
        owned: list[object] = []
        try:
            return self._encode_conversations(
                [
                    self._conversation(self._decode_image_bytes(image, owned), image=True)
                    for image in images
                ],
                batch_size=batch_size,
            )
        finally:
            for value in owned:
                close = getattr(value, "close", None)
                if callable(close):
                    close()


def build_configured_multimodal_encoder(
    *, device: str | None = None, batch_size: int = 1
) -> MultimodalEncoder:
    """Construct the explicitly selected native multimodal encoder.

    ``none`` is intentionally not a fallback model: callers must opt in to a
    native encoder rather than accidentally loading a large checkpoint.
    """
    backend = configured_multimodal_backend()
    if backend == "vllm":
        from .multimodal_runtime import (
            configured_embedding_crop_token_budget,
            configured_embedding_gpu_memory_utilization,
            configured_embedding_max_model_len,
            configured_embedding_vllm_enforce_eager,
            configured_embedding_vllm_max_num_seqs,
            configured_vllm_allowed_hosts,
            configured_vllm_image,
            configured_vllm_token,
            configured_vllm_url,
        )
        from .vllm_remote import VllmEmbeddingSettings, VllmMultimodalEncoder

        vllm_url = configured_vllm_url()
        vllm_token = configured_vllm_token()
        vllm_image = configured_vllm_image()
        allowed_hosts = configured_vllm_allowed_hosts()
        revision = configured_multimodal_revision()
        if not vllm_url or not vllm_token or not vllm_image or not allowed_hosts:
            raise ValueError(
                "vLLM requires LLM_WIKI_EMBEDDING_VLLM_URL, "
                "LLM_WIKI_EMBEDDING_VLLM_TOKEN, "
                "LLM_WIKI_EMBEDDING_VLLM_IMAGE, and "
                "LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS"
            )
        if not revision:
            raise ValueError("vLLM requires LLM_WIKI_MULTIMODAL_MODEL_REVISION")
        dimension = configured_multimodal_dimension()
        return VllmMultimodalEncoder(
            VllmEmbeddingSettings(
                url=vllm_url,
                token=vllm_token,
                image_digest=vllm_image,
                model=configured_multimodal_model(),
                model_revision=revision,
                dimension=dimension,
                instruction=os.environ.get(
                    "LLM_WIKI_MULTIMODAL_INSTRUCTION",
                    "Represent the user's input.",
                ),
                timeout_seconds=float(os.environ.get("LLM_WIKI_EMBEDDING_VLLM_TIMEOUT_SECONDS", "30")),
                allowed_hosts=allowed_hosts,
                max_model_len=configured_embedding_max_model_len(),
                crop_token_budget=configured_embedding_crop_token_budget(),
                gpu_memory_utilization=configured_embedding_gpu_memory_utilization(),
                enforce_eager=configured_embedding_vllm_enforce_eager(),
                max_num_seqs=configured_embedding_vllm_max_num_seqs(),
            )
        )

    service_url = os.environ.get(
        "LLM_WIKI_EMBEDDING_SERVICE_URL",
        os.environ.get("LLM_WIKI_EMBEDDING_SERVICE_URL", ""),
    ).strip()
    if service_url:
        from .multimodal_remote import (
            EmbeddingServiceSettings,
            RemoteMultimodalEncoder,
        )
        from .multimodal_runtime import (
            configured_embedding_crop_token_budget,
            configured_embedding_max_model_len,
            configured_embedding_service_allowed_hosts,
            configured_embedding_service_max_request_bytes,
            configured_embedding_service_timeout,
            configured_embedding_service_token,
        )

        allowed_hosts = configured_embedding_service_allowed_hosts()
        if not allowed_hosts:
            raise ValueError(
                "LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS must explicitly allow "
                "the configured Embedding Service host"
            )

        instruction = os.environ.get(
            "LLM_WIKI_EMBEDDING_INSTRUCTION",
            os.environ.get("LLM_WIKI_EMBEDDING_INSTRUCTION", "Represent the user's input."),
        )
        profile = MultimodalEmbeddingProfile(
            provider="transformers",
            model=configured_multimodal_model(),
            model_revision=configured_multimodal_revision(),
            embedding="dense",
            dimension=configured_multimodal_dimension(),
            metric="dot",
            preprocessing_fingerprint=(
                f"qwen3-vl:dense:768:{configured_embedding_max_model_len()}:"
                f"crop={configured_embedding_crop_token_budget()}:"
                f"{sha256(instruction.encode('utf-8')).hexdigest()[:16]}"
            ),
            max_sequence_length=configured_embedding_max_model_len(),
            crop_token_budget=configured_embedding_crop_token_budget(),
            tokenizer_fingerprint="service-tokenize-v1",
            crop_policy="service_token_count_character_prefix",
        )
        return RemoteMultimodalEncoder(
            profile,
            EmbeddingServiceSettings(
                url=service_url,
                token=configured_embedding_service_token(),
                timeout_seconds=configured_embedding_service_timeout(),
                max_request_bytes=configured_embedding_service_max_request_bytes(),
                expected_profile_fingerprint=(
                    os.environ.get("LLM_WIKI_EMBEDDING_PROFILE_FINGERPRINT")
                    or None
                ),
                allowed_hosts=allowed_hosts,
            ),
        )

    if backend == "none":
        raise ValueError(
            "native multimodal encoding is disabled; set "
            "LLM_WIKI_MULTIMODAL_BACKEND=transformers to enable Qwen3-VL"
        )
    if backend == "legacy-colqwen":
        return ColQwenNativeEncoder.from_pretrained(
            configured_multimodal_model()
            if configured_multimodal_model() != DEFAULT_QWEN3_VL_MODEL
            else DEFAULT_COLQWEN_MODEL,
            device=device,
            batch_size=batch_size,
        )
    raise ValueError(
        "Qwen3-VL production inference requires "
        "LLM_WIKI_EMBEDDING_SERVICE_URL; local Transformers inference is "
        "available only through developer/test tooling"
    )

def embed_pending(
    store: MultimodalProjectionStore,
    encoder: MultimodalEncoder,
    *,
    batch_size: int | None = None,
    max_units: int | None = None,
    resolver: AssetResolver | None = None,
    workspace_id: str | None = None,
) -> int:
    """Promote captured Stage-1 units incrementally and crash-safely."""

    if max_units is not None and max_units <= 0:
        raise ValueError("max_units must be positive when supplied")

    pending = [
        unit
        for unit in store.pending_units(workspace_id=workspace_id)
    ]
    if max_units is not None:
        pending = pending[:max_units]
    if not pending:
        return 0
    resolved_pending: list[MultimodalSourceUnit] = []
    resolved_assets: dict[str, bytes] = {}
    for unit in pending:
        if unit.content_ref is None or unit.modality == "text":
            resolved_pending.append(unit)
            continue
        if resolver is None:
            raise ProjectionIntegrityError(
                f"asset resolver is required to verify pending unit {unit.view_id!r}"
            )
        raw = resolver.resolve(unit)
        read = getattr(raw, "read", None)
        if callable(read):
            raw = cast(Callable[[], object], read)()
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise ProjectionIntegrityError(
                f"asset resolver returned non-byte content for {unit.view_id!r}"
            )
        raw_bytes = bytes(raw)
        resolved_digest = sha256(raw_bytes).hexdigest()
        if unit.asset_sha256 and unit.asset_sha256.lower() != resolved_digest:
            raise ProjectionIntegrityError(
                f"asset digest does not match resolved bytes for {unit.view_id!r}"
            )
        resolved_pending.append(replace(unit, asset_sha256=resolved_digest))
        resolved_assets[unit.view_id] = raw_bytes
    pending = resolved_pending
    encode_resolver = resolver
    if resolved_assets:
        class _ResolvedAssetResolver:
            def resolve(self, unit: MultimodalSourceUnit) -> object:
                if unit.view_id in resolved_assets:
                    return resolved_assets[unit.view_id]
                if resolver is None:
                    raise ProjectionIntegrityError("asset resolver is unavailable")
                return resolver.resolve(unit)

        encode_resolver = _ResolvedAssetResolver()
    vectors = _normalise_sets(
        encoder.encode_documents(pending, batch_size=batch_size, resolver=encode_resolver),
        profile=encoder.profile,
    )
    if len(vectors) != len(pending):
        raise ProjectionIntegrityError("encoder returned a different number of document embeddings")
    for unit, embedding in zip(pending, vectors):
        store.upsert_embedding(unit, embedding, profile=encoder.profile)
    return len(pending)


__all__ = [
    "DEFAULT_COLQWEN_MODEL",
    "DEFAULT_COLQWEN_REVISION",
    "DEFAULT_QWEN3_VL_MODEL",
    "QWEN3_VL_MAX_DIMENSION",
    "QWEN3_VL_MIN_DIMENSION",
    "AssetResolver",
    "ChromaMultimodalProjectionStore",
    "ColQwenNativeEncoder",
    "EmbeddingProfileMismatch",
    "EmbeddingSet",
    "FakeMultimodalEncoder",
    "InMemoryMultimodalProjectionStore",
    "MultimodalEmbeddingProfile",
    "MultimodalEncoder",
    "MultimodalImageQueryEncoder",
    "MultimodalProjectionStore",
    "MultimodalSearchHit",
    "MultimodalSourceUnit",
    "PgVectorMultimodalProjectionStore",
    "ProjectionIntegrityError",
    "Qwen3VLDenseEncoder",
    "SQLiteMultimodalProjectionStore",
    "build_configured_multimodal_encoder",
    "embed_pending",
    "score_embedding_sets",
    "to_core_embedding_profile",
]
