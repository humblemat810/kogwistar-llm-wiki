from __future__ import annotations

from dataclasses import replace

import pytest

from kogwistar.engine_core import (
    EmbeddingReference,
    MultimodalSpan,
    PinnedLogicalRef,
)
from kogwistar.logical_refs import LogicalRef
from kogwistar_llm_wiki.embeddings.multimodal_projection import (
    EmbeddingProfileMismatch,
    InMemoryMultimodalProjectionStore,
    MultimodalEmbeddingProfile,
    MultimodalSourceUnit,
    ProjectionIntegrityError,
    to_core_embedding_profile,
)
from kogwistar_llm_wiki.embeddings.multimodal_dereference import (
    EmbeddingReferenceDereferencer,
)
from kogwistar_llm_wiki.embeddings.multimodal_sources import (
    audio_interval_unit,
    video_region_track_unit,
)


def test_wire_profile_maps_to_core_profile_without_dimension_only_identity() -> None:
    profile = MultimodalEmbeddingProfile(
        provider="fake",
        model="colqwen",
        embedding="late_interaction",
        dimension=128,
        metric="dot",
        model_revision="r1",
        preprocessing_fingerprint="crop:v1",
        max_image_patches=200,
    )
    core = to_core_embedding_profile(profile)
    assert core.embedding_kind == "late_interaction"
    assert core.similarity_metric == "ip"
    assert core.fingerprint != to_core_embedding_profile(
        MultimodalEmbeddingProfile(
            provider="fake",
            model="other-model",
            embedding="late_interaction",
            dimension=128,
            metric="dot",
            model_revision="r1",
            preprocessing_fingerprint="crop:v1",
            max_image_patches=200,
        )
    ).fingerprint


def test_audio_and_video_track_units_create_typed_core_spans() -> None:
    audio = audio_interval_unit(
        workspace_id="workspace-a",
        source_id="audio-1",
        revision_id="rev-1",
        start_ms=100,
        end_ms=500,
        content_ref="lake://audio-1",
    )
    assert audio.to_multimodal_span().locator.kind == "temporal_interval"

    video = video_region_track_unit(
        workspace_id="workspace-a",
        source_id="video-1",
        revision_id="rev-1",
        start_ms=0,
        end_ms=1000,
        track_manifest_ref="lake://tracks/video-1.json",
        track_manifest_sha256="a" * 64,
        content_ref="lake://video-1",
    )
    span = video.to_multimodal_span()
    assert isinstance(span, MultimodalSpan)
    assert span.locator.kind == "video_region_track"


def test_search_unit_can_carry_one_reference_for_many_late_interaction_vectors() -> None:
    unit = MultimodalSourceUnit(
        view_id="view-1",
        workspace_id="workspace-a",
        source_id="image-1",
        source_revision_id="rev-1",
        modality="image",
        locator={"kind": "whole_image"},
        content_ref="lake://image-1",
    )
    reference = EmbeddingReference(
        source_namespace="workspace-a",
        profile_fingerprint="b" * 64,
        embedding_set_id=unit.view_id,
        span=unit.to_multimodal_span(),
        targets=(
            PinnedLogicalRef(
                logical_ref=LogicalRef(
                    "workspace-a", "artifact", "source-map:image-1"
                ),
                role="source_map",
                revision_id="rev-1",
            ),
        ),
    )
    persisted = replace(unit, embedding_reference=reference)
    assert persisted.embedding_reference is not None
    assert persisted.to_payload()["embedding_reference"] is not None


def test_projection_rejects_reference_from_another_embedding_space() -> None:
    profile = MultimodalEmbeddingProfile(
        provider="fake",
        model="colqwen",
        embedding="late_interaction",
        dimension=8,
    )
    unit = MultimodalSourceUnit(
        view_id="view-1",
        workspace_id="workspace-a",
        source_id="image-1",
        source_revision_id="rev-1",
        modality="image",
        locator={"kind": "whole_image"},
        content_ref="lake://image-1",
    )
    invalid = replace(
        unit,
        embedding_reference=EmbeddingReference(
            source_namespace="workspace-a",
            profile_fingerprint="b" * 64,
            embedding_set_id=unit.view_id,
            span=unit.to_multimodal_span(),
            targets=(
                PinnedLogicalRef(
                    logical_ref=LogicalRef(
                        "workspace-a", "artifact", "source-map:image-1"
                    ),
                    role="source_map",
                    revision_id="rev-1",
                ),
            ),
        ),
    )
    store = InMemoryMultimodalProjectionStore(scope="workspace-a", profile=profile)
    with pytest.raises(EmbeddingProfileMismatch):
        store.capture(invalid)
    assert store.projection_scope.endswith(profile.fingerprint)


def test_legacy_locator_is_readable_but_not_indexable() -> None:
    profile = MultimodalEmbeddingProfile(
        provider="fake",
        model="colqwen",
        embedding="late_interaction",
        dimension=8,
    )
    unit = MultimodalSourceUnit(
        view_id="view-legacy",
        workspace_id="workspace-a",
        source_id="image-legacy",
        source_revision_id="rev-1",
        modality="image",
        locator={"kind": "unknown_historical_locator", "value": "opaque"},
        content_ref="lake://image-legacy",
    )
    store = InMemoryMultimodalProjectionStore(scope="workspace-a", profile=profile)
    store.capture(unit)
    with pytest.raises(ProjectionIntegrityError, match="converted"):
        store.upsert_embedding(unit, ((1.0,) * 8,), profile=profile)


class _Resolver:
    def __init__(self, *, source: bool = True, target: bool = True) -> None:
        self.source = source
        self.target = target

    def resolve_source_map(self, reference: PinnedLogicalRef) -> bool:
        return self.source

    def authorize_target(self, reference: PinnedLogicalRef) -> bool:
        return self.target


def test_dereference_checks_source_map_first_and_enforces_namespace() -> None:
    unit = MultimodalSourceUnit(
        view_id="view-1",
        workspace_id="workspace-a",
        source_id="image-1",
        source_revision_id="rev-1",
        modality="image",
        locator={"kind": "whole_image"},
        content_ref="lake://image-1",
    )
    reference = EmbeddingReference(
        source_namespace="workspace-a",
        profile_fingerprint="b" * 64,
        embedding_set_id=unit.view_id,
        span=unit.to_multimodal_span(),
        targets=(
            PinnedLogicalRef(
                logical_ref=LogicalRef(
                    "workspace-a", "artifact", "source-map:image-1"
                ),
                role="source_map",
                revision_id="rev-1",
            ),
            PinnedLogicalRef(
                logical_ref=LogicalRef("workspace-a", "node", "node:rabbit"),
                role="semantic",
                mode="live",
            ),
        ),
    )
    assert (
        EmbeddingReferenceDereferencer(_Resolver()).resolve(
            reference, workspace_id="workspace-a"
        ).status
        == "available"
    )
    assert (
        EmbeddingReferenceDereferencer(_Resolver(source=False)).resolve(
            reference, workspace_id="workspace-a"
        ).status
        == "stale"
    )
    assert (
        EmbeddingReferenceDereferencer(_Resolver()).resolve(
            reference, workspace_id="workspace-b"
        ).status
        == "unauthorized"
    )
