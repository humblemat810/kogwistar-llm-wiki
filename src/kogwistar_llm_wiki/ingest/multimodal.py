"""Multimodal capture, embedding, and search helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import cast

from ..embeddings.multimodal_projection import (
    AssetResolver,
    MultimodalImageQueryEncoder,
    MultimodalSearchHit,
    MultimodalSourceUnit,
    embed_pending,
)
from ..embeddings.multimodal_sources import MultimodalSourceBundle, build_source_bundle
from ..maintenance.maintenance_guards import source_revision_document_id
from ..utils import _temporary_namespace
from .contracts import IngestPipelineHost


class MultimodalIngestMixin:
    """Product-facing multimodal projection operations."""

    def capture_multimodal_units(self: IngestPipelineHost, units: Sequence[MultimodalSourceUnit]) -> int:
        """Capture validated retrieval views into Stage 1.

        Multimodal projection storage is opt-in and remains separate from the
        canonical text graph ingestion path.
        """
        if self.multimodal_projection_store is None:
            raise RuntimeError("multimodal projection is not configured for this pipeline")
        if not units:
            return 0
        profile = getattr(self.multimodal_projection_store, "profile", None)
        if profile is None:
            raise RuntimeError("multimodal projection store does not expose its profile")
        seen: set[tuple[str, str]] = set()
        for unit in units:
            key = (unit.workspace_id, unit.view_id)
            if key in seen:
                raise ValueError(f"duplicate multimodal source view {unit.view_id!r}")
            seen.add(key)
            existing = self.multimodal_projection_store.get(
                unit.view_id,
                profile=profile,
                workspace_id=unit.workspace_id,
            )
            if existing is not None and existing.to_payload() != unit.to_payload():
                old_payload = existing.to_payload()
                new_payload = unit.to_payload()
                old_digest = old_payload.pop("asset_sha256", None)
                new_digest = new_payload.pop("asset_sha256", None)
                if old_digest is not None or new_digest is None or old_payload != new_payload:
                    raise ValueError(f"source view {unit.view_id!r} was changed in place")
        capture_many = getattr(self.multimodal_projection_store, "capture_many", None)
        if callable(capture_many):
            capture_many(tuple(units))
        else:
            for unit in units:
                self.multimodal_projection_store.capture(unit)
        return len(units)

    def resolve_multimodal_source_map(
        self: IngestPipelineHost, unit: MultimodalSourceUnit
    ) -> Mapping[str, object] | None:
        """Resolve the immutable source-map record for a projection unit.

        The source graph is the authority for source bytes and revision
        identity. Projection callers may request a view, but they cannot make
        the view authoritative by supplying their own text or asset digest.
        """
        source_namespace = self.namespaces_for(unit.workspace_id).source_space
        with _temporary_namespace(self.engines.kg, source_namespace):
            nodes = self.engines.kg.read.get_nodes(
                ids=[unit.source_revision_id],
                include=["documents", "metadatas"],
                limit=1,
            )
            if not nodes:
                nodes = self.engines.kg.read.get_nodes(
                    where={
                        "$and": [
                            {"artifact_kind": "source_revision"},
                            {"source_revision_id": unit.source_revision_id},
                            {"source_document_id": unit.source_id},
                        ]
                    },
                    include=["documents", "metadatas"],
                    limit=10,
                )
            node = nodes[0] if nodes else None
            revision_document = None
            if node is None:
                revision_document_id = source_revision_document_id(
                    workspace_id=unit.workspace_id,
                    source_document_id=unit.source_id,
                    revision_id=unit.source_revision_id,
                )
                try:
                    revision_document = self.engines.kg.read.get_document(revision_document_id)
                except (KeyError, LookupError, ValueError):
                    return None
            metadata = dict(
                getattr(node, "metadata", None)
                or getattr(revision_document, "metadata", None)
                or {}
            )
            declared_workspace = str(metadata.get("workspace_id") or "").strip()
            if declared_workspace and declared_workspace != unit.workspace_id:
                return None
            source_id = str(
                metadata.get("logical_source_document_id")
                or metadata.get("source_document_id")
                or ""
            ).strip()
            revision_id = str(
                metadata.get("source_revision_id")
                or getattr(node, "id", "")
                or unit.source_revision_id
            )
            if source_id != unit.source_id or revision_id != unit.source_revision_id:
                return None
            revision_document_id = str(
                metadata.get("revision_document_id")
                or metadata.get("source_revision_document_id")
                or getattr(node, "doc_id", "")
                or getattr(revision_document, "id", "")
                or ""
            ).strip()
            raw_text = metadata.get("source_raw_text")
            if raw_text is None and revision_document_id:
                try:
                    raw_text = (
                        revision_document.content
                        if revision_document is not None
                        else self.engines.kg.read.get_document(revision_document_id).content
                    )
                except (KeyError, LookupError, ValueError):
                    raw_text = None
            source_digest = str(metadata.get("source_digest") or "").strip() or None
            if isinstance(raw_text, str) and source_digest is None:
                source_digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            return {
                "workspace_id": unit.workspace_id,
                "source_namespace": source_namespace,
                "source_id": source_id,
                "source_revision_id": revision_id,
                "revision_document_id": revision_document_id,
                "raw_text": raw_text,
                "source_digest": source_digest,
                "content_ref": metadata.get("source_content_ref") or metadata.get("content_ref"),
                "asset_sha256": metadata.get("source_asset_sha256") or metadata.get("asset_sha256"),
            }

    def capture_multimodal_source(
        self: IngestPipelineHost,
        *,
        workspace_id: str,
        source_id: str,
        source_revision_id: str,
        source_format: str,
        source_uri: str | None = None,
        raw_text: str | None = None,
        content_ref: str | None = None,
        manifest: Mapping[str, object] | None = None,
        max_chars: int = 4000,
    ) -> MultimodalSourceBundle:
        """Decompose and capture one source revision into Stage 1 views."""
        bundle = build_source_bundle(
            workspace_id=workspace_id,
            source_id=source_id,
            source_revision_id=source_revision_id,
            source_format=source_format,
            source_uri=source_uri,
            raw_text=raw_text,
            content_ref=content_ref,
            manifest=manifest,
            max_chars=max_chars,
        )
        self.capture_multimodal_units(bundle.units)
        return bundle

    def embed_multimodal_pending(
        self: IngestPipelineHost,
        *,
        batch_size: int | None = None,
        max_units: int | None = None,
        resolver: AssetResolver | None = None,
        workspace_id: str | None = None,
    ) -> int:
        """Promote pending Stage-1 views using the configured encoder."""
        if self.multimodal_projection_store is None or self.multimodal_encoder is None:
            raise RuntimeError("multimodal projection and encoder must both be configured")
        return embed_pending(
            self.multimodal_projection_store,
            self.multimodal_encoder,
            batch_size=batch_size,
            max_units=max_units,
            resolver=resolver,
            workspace_id=workspace_id,
        )

    def search_multimodal(
        self: IngestPipelineHost,
        query: str,
        *,
        limit: int = 10,
    ) -> list[MultimodalSearchHit]:
        """Run a grounded multimodal projection query after Stage 2."""
        if self.multimodal_projection_store is None or self.multimodal_encoder is None:
            raise RuntimeError("multimodal projection and encoder must both be configured")
        query_vectors = self.multimodal_encoder.encode_queries([query])
        if len(query_vectors) != 1:
            raise ValueError("multimodal encoder returned an invalid query result")
        return self.multimodal_projection_store.search(
            query_vectors[0],
            profile=self.multimodal_encoder.profile,
            limit=limit,
        )

    def search_multimodal_image(
        self: IngestPipelineHost,
        images: Sequence[object],
        *,
        limit: int = 10,
        batch_size: int | None = None,
    ) -> list[MultimodalSearchHit]:
        """Search native image projections without converting images to text."""
        if self.multimodal_projection_store is None or self.multimodal_encoder is None:
            raise RuntimeError("multimodal projection and encoder must both be configured")
        if not isinstance(self.multimodal_encoder, MultimodalImageQueryEncoder):
            raise TypeError("configured multimodal encoder does not support image queries")
        query_vectors = self.multimodal_encoder.encode_image_queries(
            images, batch_size=batch_size
        )
        if len(query_vectors) != 1:
            raise ValueError("image query requires exactly one image")
        return self.multimodal_projection_store.search(
            query_vectors[0],
            profile=self.multimodal_encoder.profile,
            limit=limit,
        )

    def search_multimodal_mixed(
        self: IngestPipelineHost,
        *,
        text_queries: Sequence[str] = (),
        images: Sequence[object] = (),
        limit: int = 10,
        text_weight: float = 1.0,
        image_weight: float = 1.0,
    ) -> list[MultimodalSearchHit]:
        """Fuse bounded text/image searches without averaging incompatible inputs."""
        if self.multimodal_projection_store is None or self.multimodal_encoder is None:
            raise RuntimeError("multimodal projection and encoder must both be configured")
        if limit <= 0:
            return []
        if text_weight < 0 or image_weight < 0 or (text_weight == 0 and image_weight == 0):
            raise ValueError("mixed query weights must be non-negative and not both zero")
        texts = tuple(text for text in text_queries if str(text).strip())
        image_values = tuple(images)
        if not texts and not image_values:
            raise ValueError("mixed query requires text_queries or images")
        weighted_hits: dict[str, tuple[float, float, MultimodalSearchHit]] = {}

        def add_hits(
            hits: Sequence[MultimodalSearchHit],
            *,
            weight: float,
            query_key: str,
        ) -> None:
            if weight == 0:
                return
            for hit in hits:
                score, total_weight, existing = weighted_hits.get(
                    hit.view_id, (0.0, 0.0, hit)
                )
                weighted_hits[hit.view_id] = (
                    score + float(hit.score) * weight,
                    total_weight + weight,
                    MultimodalSearchHit(
                        view_id=existing.view_id,
                        score=existing.score,
                        source_id=existing.source_id,
                        source_revision_id=existing.source_revision_id,
                        modality=existing.modality,
                        locator=dict(existing.locator),
                        metadata={
                            **existing.metadata,
                            "query_contributions": {
                                **(
                                    dict(raw_contributions)
                                    if isinstance(
                                        raw_contributions := existing.metadata.get("query_contributions"),
                                        Mapping,
                                    )
                                    else {}
                                ),
                                query_key: float(hit.score),
                            },
                        },
                    ),
                )

        for index, text in enumerate(texts):
            add_hits(
                cast(
                    Sequence[MultimodalSearchHit],
                    self.search_multimodal(text, limit=limit),
                ),
                weight=text_weight,
                query_key=f"text:{index}",
            )
        if image_values:
            if not isinstance(self.multimodal_encoder, MultimodalImageQueryEncoder):
                raise RuntimeError("configured multimodal encoder does not support image queries")
            image_vectors = self.multimodal_encoder.encode_image_queries(image_values)
            if len(image_vectors) != len(image_values):
                raise ValueError("image query encoder returned an invalid result count")
            for index, vectors in enumerate(image_vectors):
                add_hits(
                    self.multimodal_projection_store.search(
                        vectors,
                        profile=self.multimodal_encoder.profile,
                        limit=limit,
                    ),
                    weight=image_weight,
                    query_key=f"image:{index}",
                )
        ranked: list[MultimodalSearchHit] = []
        for score, total_weight, hit in weighted_hits.values():
            ranked.append(
                MultimodalSearchHit(
                    view_id=hit.view_id,
                    score=score / total_weight,
                    source_id=hit.source_id,
                    source_revision_id=hit.source_revision_id,
                    modality=hit.modality,
                    locator=dict(hit.locator),
                    metadata={
                        **hit.metadata,
                        "query_fusion": "weighted_mean_of_bounded_independent_queries",
                    },
                )
            )
        ranked.sort(key=lambda hit: (-hit.score, hit.view_id))
        return ranked[:limit]
