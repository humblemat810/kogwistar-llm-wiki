"""Application-owned multimodal capture and projection.

The normal Kogwistar node and edge embeddings remain single-vector fields.
This module stores multimodal retrieval views separately, which lets a
ColQwen-style encoders can return one vector per token or image patch without
changing the graph schema.

Qwen3-VL is represented separately as a normalized dense vector per source
unit.  The representation is part of the profile fingerprint, so equal
dimensions never make late-interaction and dense spaces interchangeable.

Stage 1 stores only source-unit metadata and references. Stage 2 adds the
embedding set after the source reference has been captured and validated.
SQLite is deliberately a small portable reference store; a production Chroma
or pgvector adapter can implement the same protocol later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
from io import BytesIO
from math import sqrt
from pathlib import Path
import os
import sqlite3
from typing import Literal, Protocol, runtime_checkable

from llm_wiki_representation_contract import (
    EmbeddingProfile as MultimodalEmbeddingProfile,
    EmbeddingSet,
    EmbeddingRepresentation,
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


class EmbeddingProfileMismatch(ValueError):
    """Raised before a projection can mix incompatible embedding profiles."""


class ProjectionIntegrityError(ValueError):
    """Raised when a stage transition or vector payload is invalid."""


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
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.view_id or not self.workspace_id or not self.source_id or not self.source_revision_id:
            raise ValueError("multimodal source units require stable identity fields")
        if self.modality not in {"text", "image", "pdf_page", "table", "chart", "webpage", "video_frame"}:
            raise ValueError(f"unsupported source modality {self.modality!r}")
        if not self.content_ref and not self.text:
            raise ValueError("a source unit requires content_ref or text")

    def to_payload(self) -> dict[str, object]:
        return {
            "view_id": self.view_id,
            "workspace_id": self.workspace_id,
            "source_id": self.source_id,
            "source_revision_id": self.source_revision_id,
            "modality": self.modality,
            "locator": dict(self.locator),
            "content_ref": self.content_ref,
            "text": self.text,
            "asset_sha256": self.asset_sha256,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "MultimodalSourceUnit":
        return cls(
            view_id=str(payload["view_id"]),
            workspace_id=str(payload["workspace_id"]),
            source_id=str(payload["source_id"]),
            source_revision_id=str(payload["source_revision_id"]),
            modality=str(payload["modality"]),  # type: ignore[arg-type]
            locator=dict(payload.get("locator") or {}),
            content_ref=str(payload["content_ref"]) if payload.get("content_ref") else None,
            text=str(payload["text"]) if payload.get("text") else None,
            asset_sha256=str(payload["asset_sha256"]) if payload.get("asset_sha256") else None,
            metadata=dict(payload.get("metadata") or {}),
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


@runtime_checkable
class MultimodalEncoder(Protocol):
    @property
    def profile(self) -> MultimodalEmbeddingProfile: ...

    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]: ...

    def encode_documents(
        self,
        units: Sequence[MultimodalSourceUnit],
        *,
        batch_size: int | None = None,
        resolver: "AssetResolver | None" = None,
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


@runtime_checkable
class MultimodalProjectionStore(Protocol):
    @property
    def profile(self) -> MultimodalEmbeddingProfile: ...

    def capture(self, unit: MultimodalSourceUnit) -> None: ...

    def pending_units(self) -> Sequence[MultimodalSourceUnit]: ...

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
    ) -> list[MultimodalSearchHit]: ...


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
    if profile.representation in {"single_vector", "dense"} and any(len(item) != 1 for item in result):
        raise ProjectionIntegrityError(
            f"{profile.representation} profiles require exactly one vector per view"
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

    if len(query) != 1 and profile.representation in {"single_vector", "dense"}:
        raise ProjectionIntegrityError(f"{profile.representation} query must contain one vector")
    if profile.representation in {"single_vector", "dense"}:
        return _vector_score(query[0], document[0], profile.metric)
    # ColBERT/ColQwen MaxSim: each query token chooses its best document token.
    return sum(
        max(_vector_score(query_vector, document_vector, profile.metric) for document_vector in document)
        for query_vector in query
    )


class InMemoryMultimodalProjectionStore:
    """Deterministic store used by tests and small local demonstrations."""

    def __init__(self, *, scope: str, profile: MultimodalEmbeddingProfile) -> None:
        self.scope = str(scope)
        self.profile = profile
        self._units: dict[str, MultimodalSourceUnit] = {}
        self._embeddings: dict[str, EmbeddingSet] = {}

    def _check_profile(self, profile: MultimodalEmbeddingProfile) -> None:
        if profile.fingerprint != self.profile.fingerprint:
            raise EmbeddingProfileMismatch(
                f"multimodal projection {self.scope!r} is bound to {self.profile.fingerprint}, "
                f"not {profile.fingerprint}"
            )

    def capture(self, unit: MultimodalSourceUnit) -> None:
        existing = self._units.get(unit.view_id)
        if existing is not None and existing.to_payload() != unit.to_payload():
            raise ProjectionIntegrityError(f"source view {unit.view_id!r} was changed in place")
        self._units[unit.view_id] = unit

    def upsert_embedding(self, unit: MultimodalSourceUnit, vectors: object, *, profile: MultimodalEmbeddingProfile) -> None:
        self._check_profile(profile)
        normalised = _normalise_embedding_set(vectors, dimension=profile.dimension)
        if profile.representation == "single_vector" and len(normalised) != 1:
            raise ProjectionIntegrityError("single_vector profiles require exactly one vector per view")
        self.capture(unit)
        self._embeddings[unit.view_id] = normalised

    def stage_counts(self) -> dict[str, int]:
        embedded = len(self._embeddings)
        return {"stage1": len(self._units), "stage2": embedded, "pending_stage2": len(self._units) - embedded}

    def pending_units(self) -> Sequence[MultimodalSourceUnit]:
        return tuple(unit for view_id, unit in self._units.items() if view_id not in self._embeddings)

    def search(self, query_vectors: object, *, profile: MultimodalEmbeddingProfile, limit: int = 10) -> list[MultimodalSearchHit]:
        self._check_profile(profile)
        query = _normalise_embedding_set(query_vectors, dimension=profile.dimension)
        scored = [
            (score_embedding_sets(query, vectors, profile=profile), self._units[view_id])
            for view_id, vectors in self._embeddings.items()
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
        for row in self._connection.execute("SELECT unit_json, embedding_json FROM multimodal_source_unit"):
            unit = MultimodalSourceUnit.from_payload(json.loads(str(row["unit_json"])))
            self._units[unit.view_id] = unit
            if row["embedding_json"]:
                self._embeddings[unit.view_id] = _normalise_embedding_set(
                    json.loads(str(row["embedding_json"])), dimension=self.profile.dimension
                )

    def capture(self, unit: MultimodalSourceUnit) -> None:
        super().capture(unit)
        existing = self._connection.execute(
            "SELECT unit_json FROM multimodal_source_unit WHERE view_id = ?", (unit.view_id,)
        ).fetchone()
        if existing is not None and json.loads(str(existing["unit_json"])) != unit.to_payload():
            raise ProjectionIntegrityError(f"source view {unit.view_id!r} was changed in place")
        self._connection.execute(
            """
            INSERT INTO multimodal_source_unit(view_id, unit_json, stage, embedding_json)
            VALUES (?, ?, 'stage1', NULL)
            ON CONFLICT(view_id) DO UPDATE SET unit_json = excluded.unit_json
            """,
            (unit.view_id, json.dumps(unit.to_payload(), sort_keys=True)),
        )
        self._connection.commit()

    def upsert_embedding(self, unit: MultimodalSourceUnit, vectors: object, *, profile: MultimodalEmbeddingProfile) -> None:
        self._check_profile(profile)
        normalised = _normalise_embedding_set(vectors, dimension=profile.dimension)
        if profile.representation == "single_vector" and len(normalised) != 1:
            raise ProjectionIntegrityError("single_vector profiles require exactly one vector per view")
        self.capture(unit)
        self._embeddings[unit.view_id] = normalised
        self._connection.execute(
            "UPDATE multimodal_source_unit SET stage = 'stage2', embedding_json = ? WHERE view_id = ?",
            (json.dumps(normalised), unit.view_id),
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
            import chromadb
        except ImportError as exc:
            raise RuntimeError(
                "Chroma multimodal projection requires the chromadb extra"
            ) from exc
        self.persist_directory = Path(persist_directory).expanduser().resolve()
        if max_search_vectors <= 0:
            raise ValueError("max_search_vectors must be positive")
        self.max_search_vectors = int(max_search_vectors)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        state_path = self.persist_directory / ".kogwistar-multimodal-state.sqlite3"
        super().__init__(state_path, scope=scope, profile=profile)
        self._client = chromadb.PersistentClient(path=str(self.persist_directory))
        name = collection_name or f"mm_{sha256(str(scope).encode('utf-8')).hexdigest()[:24]}"
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
        normalised = _normalise_embedding_set(vectors, dimension=profile.dimension)
        if profile.representation == "single_vector" and len(normalised) != 1:
            raise ProjectionIntegrityError("single_vector profiles require exactly one vector per view")
        ids = [f"{unit.view_id}:{ordinal}" for ordinal in range(len(normalised))]
        metadatas = [
            {
                "view_id": unit.view_id,
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
            metadatas=metadatas,
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
            view_id = str(metadata.get("view_id") or "")
            if view_id not in self._units:
                raise ProjectionIntegrityError(f"Chroma contains an unknown source view {view_id!r}")
            grouped.setdefault(view_id, []).append((int(metadata.get("vector_ordinal", 0)), vector))
        scored: list[tuple[float, MultimodalSourceUnit]] = []
        for view_id, values in grouped.items():
            values.sort(key=lambda item: item[0])
            vectors = _normalise_embedding_set(
                [value for _, value in values], dimension=profile.dimension
            )
            scored.append((score_embedding_sets(query, vectors, profile=profile), self._units[view_id]))
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
            )
            for score, unit in scored[: max(0, int(limit))]
        ]

    def close(self) -> None:
        SQLiteMultimodalProjectionStore.close(self)


@dataclass(frozen=True, slots=True)
class FakeMultimodalEncoder:
    """Provider-free late-interaction encoder with stable test vectors."""

    profile: MultimodalEmbeddingProfile = field(
        default_factory=lambda: MultimodalEmbeddingProfile(
            provider="fake", model="fake-colqwen-compatible", representation="late_interaction", dimension=8
        )
    )

    def _vector(self, value: str, ordinal: int) -> tuple[float, ...]:
        digest = sha256(f"{value}\x00{ordinal}".encode("utf-8")).digest()
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
        model: object,
        processor: object,
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
    ) -> "ColQwenNativeEncoder":
        try:
            import sys

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

        model = ColQwen2ForRetrieval.from_pretrained(model_id, **model_kwargs).eval()
        processor = (
            AutoProcessor.from_pretrained(model_id, revision=effective_revision)
            if effective_revision
            else AutoProcessor.from_pretrained(model_id)
        )
        resolved_dimension = int(
            dimension or getattr(getattr(model, "config", None), "embedding_dim", 128)
        )
        profile = MultimodalEmbeddingProfile(
            provider="transformers",
            model=model_id,
            model_revision=effective_revision,
            representation="late_interaction",
            dimension=resolved_dimension,
            metric="dot",
            preprocessing_fingerprint=f"colqwen2:{max_image_patches}:{max_sequence_length}",
            max_sequence_length=max_sequence_length,
            max_image_patches=max_image_patches,
        )
        return cls(model, processor, profile=profile, device=selected_device, batch_size=batch_size)

    def _run(self, inputs: object) -> Sequence[EmbeddingSet]:
        import torch

        if hasattr(inputs, "to"):
            inputs = inputs.to(self.device)
        attention_mask = inputs.get("attention_mask") if hasattr(inputs, "get") else None
        with torch.inference_mode():
            output = self._model(**inputs)
        embeddings = getattr(output, "embeddings", output)
        if hasattr(embeddings, "detach"):
            embeddings = embeddings.detach().cpu()
        if attention_mask is not None and hasattr(attention_mask, "detach"):
            masks = attention_mask.detach().cpu()
            embeddings = [
                row[mask.to(dtype=torch.bool)]
                for row, mask in zip(embeddings, masks)
            ]
        return _normalise_sets(embeddings, profile=self.profile)

    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        return self._encode_text_values(queries, batch_size=batch_size)

    def _encode_text_values(
        self, values: Sequence[str], *, batch_size: int | None = None
    ) -> list[EmbeddingSet]:
        process_queries = getattr(self._processor, "__call__")
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
                    else:
                        image = Image.open(value)
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
        model: object,
        processor: object,
        *,
        profile: MultimodalEmbeddingProfile,
        device: str,
        batch_size: int = 1,
        vision_processor: object | None = None,
        instruction: str = "Represent the user's input.",
    ) -> None:
        if profile.representation != "dense":
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
    ) -> "Qwen3VLDenseEncoder":
        if not QWEN3_VL_MIN_DIMENSION <= int(dimension) <= QWEN3_VL_MAX_DIMENSION:
            raise ValueError(
                f"Qwen3-VL dimension must be between {QWEN3_VL_MIN_DIMENSION} and "
                f"{QWEN3_VL_MAX_DIMENSION}; got {dimension}"
            )
        try:
            import sys

            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor
            from qwen_vl_utils import process_vision_info
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
        model = AutoModelForMultimodalLM.from_pretrained(model_id, **model_kwargs).eval()
        processor_kwargs: dict[str, object] = {"trust_remote_code": True}
        if revision:
            processor_kwargs["revision"] = revision
        try:
            processor = AutoProcessor.from_pretrained(
                model_id, padding_side="right", **processor_kwargs
            )
        except TypeError:
            processor = AutoProcessor.from_pretrained(model_id, **processor_kwargs)
        profile = MultimodalEmbeddingProfile(
            provider="transformers",
            model=model_id,
            model_revision=revision,
            representation="dense",
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
                    for part in message.get("content", [])
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

        if hasattr(inputs, "to"):
            inputs = inputs.to(self.device)
        elif isinstance(inputs, Mapping):
            inputs = {
                key: value.to(self.device) if hasattr(value, "to") else value
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
        if hasattr(embeddings, "ndim") and embeddings.ndim == 3:
            mask = inputs.get("attention_mask") if isinstance(inputs, Mapping) else None
            if mask is None:
                embeddings = embeddings[:, -1, :]
            else:
                positions = mask.long().sum(dim=1).clamp_min(1) - 1
                embeddings = embeddings[torch.arange(embeddings.shape[0], device=embeddings.device), positions]
        if hasattr(embeddings, "ndim") and embeddings.ndim != 2:
            raise ProjectionIntegrityError("Qwen3-VL must return one dense vector per source view")
        if hasattr(embeddings, "ndim"):
            import torch.nn.functional as functional

            embeddings = functional.normalize(embeddings[..., : self.profile.dimension], p=2, dim=-1)
            # Dense profiles expose one vector per view, while the common
            # normalizer receives a set of vectors per view.
            return _normalise_sets([[row] for row in embeddings], profile=self.profile)
        else:
            # This branch also makes the adapter contract testable with a tiny
            # fake model in environments that intentionally omit Torch.
            normalised: list[list[list[float]]] = []
            for row in embeddings:
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
    service_url = os.environ.get("LLM_WIKI_REPRESENTATION_SERVICE_URL", "").strip()
    if service_url:
        from .multimodal_remote import (
            RemoteMultimodalEncoder,
            RepresentationServiceSettings,
        )
        from .multimodal_runtime import (
            configured_representation_service_allowed_hosts,
            configured_representation_service_max_request_bytes,
            configured_representation_service_timeout,
            configured_representation_service_token,
        )

        allowed_hosts = configured_representation_service_allowed_hosts()
        if not allowed_hosts:
            raise ValueError(
                "LLM_WIKI_REPRESENTATION_SERVICE_ALLOWED_HOSTS must explicitly allow "
                "the configured representation service host"
            )

        instruction = os.environ.get(
            "LLM_WIKI_REPRESENTATION_INSTRUCTION",
            "Represent the user's input.",
        )
        profile = MultimodalEmbeddingProfile(
            provider="transformers",
            model=configured_multimodal_model(),
            model_revision=configured_multimodal_revision(),
            representation="dense",
            dimension=configured_multimodal_dimension(),
            metric="dot",
            preprocessing_fingerprint=(
                f"qwen3-vl:dense:768:32768:"
                f"{sha256(instruction.encode('utf-8')).hexdigest()[:16]}"
            ),
        )
        return RemoteMultimodalEncoder(
            profile,
            RepresentationServiceSettings(
                url=service_url,
                token=configured_representation_service_token(),
                timeout_seconds=configured_representation_service_timeout(),
                max_request_bytes=configured_representation_service_max_request_bytes(),
                expected_profile_fingerprint=os.environ.get("LLM_WIKI_REPRESENTATION_PROFILE_FINGERPRINT") or None,
                allowed_hosts=allowed_hosts,
            ),
        )

    backend = configured_multimodal_backend()
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
        "LLM_WIKI_REPRESENTATION_SERVICE_URL; local Transformers inference is "
        "available only through developer/test tooling"
    )

def embed_pending(
    store: MultimodalProjectionStore,
    encoder: MultimodalEncoder,
    *,
    batch_size: int | None = None,
    resolver: AssetResolver | None = None,
) -> int:
    """Promote captured Stage-1 units incrementally and crash-safely."""

    pending = list(store.pending_units())
    if not pending:
        return 0
    vectors = _normalise_sets(
        encoder.encode_documents(pending, batch_size=batch_size, resolver=resolver),
        profile=encoder.profile,
    )
    if len(vectors) != len(pending):
        raise ProjectionIntegrityError("encoder returned a different number of document embeddings")
    for unit, embedding in zip(pending, vectors):
        store.upsert_embedding(unit, embedding, profile=encoder.profile)
    return len(pending)


__all__ = [
    "AssetResolver",
    "DEFAULT_COLQWEN_MODEL",
    "DEFAULT_COLQWEN_REVISION",
    "DEFAULT_QWEN3_VL_MODEL",
    "QWEN3_VL_MAX_DIMENSION",
    "QWEN3_VL_MIN_DIMENSION",
    "ColQwenNativeEncoder",
    "build_configured_multimodal_encoder",
    "EmbeddingProfileMismatch",
    "EmbeddingSet",
    "ChromaMultimodalProjectionStore",
    "FakeMultimodalEncoder",
    "InMemoryMultimodalProjectionStore",
    "MultimodalEmbeddingProfile",
    "MultimodalEncoder",
    "MultimodalImageQueryEncoder",
    "MultimodalProjectionStore",
    "MultimodalSearchHit",
    "MultimodalSourceUnit",
    "ProjectionIntegrityError",
    "Qwen3VLDenseEncoder",
    "SQLiteMultimodalProjectionStore",
    "embed_pending",
    "score_embedding_sets",
]
