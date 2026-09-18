"""Multimodal capture, embedding, and search helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..embeddings.multimodal_projection import (
    AssetResolver,
    MultimodalImageQueryEncoder,
    MultimodalSearchHit,
    MultimodalSourceUnit,
    embed_pending,
)
from ..embeddings.multimodal_sources import MultimodalSourceBundle, build_source_bundle


class MultimodalIngestMixin:
    """Product-facing multimodal projection operations."""

    def capture_multimodal_units(self, units: Sequence[MultimodalSourceUnit]) -> int:
        """Capture validated retrieval views into Stage 1.

        Multimodal projection storage is opt-in and remains separate from the
        canonical text graph ingestion path.
        """
        if self.multimodal_projection_store is None:
            raise RuntimeError("multimodal projection is not configured for this pipeline")
        for unit in units:
            self.multimodal_projection_store.capture(unit)
        return len(units)

    def capture_multimodal_source(
        self,
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
        self,
        *,
        batch_size: int | None = None,
        resolver: AssetResolver | None = None,
    ) -> int:
        """Promote pending Stage-1 views using the configured encoder."""
        if self.multimodal_projection_store is None or self.multimodal_encoder is None:
            raise RuntimeError("multimodal projection and encoder must both be configured")
        return embed_pending(
            self.multimodal_projection_store,
            self.multimodal_encoder,
            batch_size=batch_size,
            resolver=resolver,
        )

    def search_multimodal(
        self,
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
        self,
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
        self,
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
                                **dict(existing.metadata.get("query_contributions") or {}),
                                query_key: float(hit.score),
                            },
                        },
                    ),
                )

        for index, text in enumerate(texts):
            add_hits(
                self.search_multimodal(text, limit=limit),
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
