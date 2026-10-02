"""Lazy public facade for application-owned embedding adapters."""

from __future__ import annotations

from importlib import import_module

_EXPORT_MODULES = {
    **dict.fromkeys(
        (
            "EMBEDDING_SPACES",
            "TinyEmbeddingFunction",
            "embedding_profile",
            "resolve_embedding_function",
            "resolve_embedding_functions",
            "validate_shared_postgres_embedding_profile",
        ),
        ".embedding_config_resolver",
    ),
    **dict.fromkeys(
        (
            "EvidenceClosureResolver",
            "EvidenceClosureValidator",
            "EvidencePack",
            "EvidencePackReference",
            "GroundingComposition",
            "GroundingValidationError",
            "HigherOrderGrounding",
            "PinnedEntityRef",
            "ResolvedEntityGrounding",
            "SourceEvidenceRef",
        ),
        ".multimodal_grounding",
    ),
    **dict.fromkeys(
        (
            "DereferenceStatus",
            "EmbeddingDereferenceResult",
            "EmbeddingReferenceDereferencer",
            "EmbeddingReferenceResolver",
        ),
        ".multimodal_dereference",
    ),
    **dict.fromkeys(
        (
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
            "SQLiteMultimodalProjectionStore",
            "embed_pending",
            "score_embedding_sets",
            "to_core_embedding_profile",
        ),
        ".multimodal_projection",
    ),
    **dict.fromkeys(
        (
            "EmbeddingProtocolError",
            "EmbeddingServiceError",
            "EmbeddingServiceSettings",
            "EmbeddingServiceUnavailable",
            "RemoteMultimodalEncoder",
        ),
        ".multimodal_remote",
    ),
    **dict.fromkeys(
        (
            "LocalFileAssetResolver",
            "MappingAssetResolver",
            "MultimodalSourceBundle",
            "build_source_bundle",
            "manifest_units",
            "pdf_manifest_units",
            "split_text_units",
            "webpage_units",
            "audio_interval_unit",
            "video_interval_unit",
            "video_region_track_unit",
        ),
        ".multimodal_sources",
    ),
    **dict.fromkeys(
        ("VllmEmbeddingSettings", "VllmMultimodalEncoder"), ".vllm_remote"
    ),
    **dict.fromkeys(
        (
            "EvidenceEvent",
            "EvidenceSubscription",
            "FeedState",
            "MultimodalRecallTiming",
            "MultimodalRetrievalSidecar",
            "SynchronousMultimodalRecall",
            "synchronous_multimodal_recall",
        ),
        ".retrieval_experiment",
    ),
}

__all__ = [
    "EMBEDDING_SPACES",
    "AssetResolver",
    "ChromaMultimodalProjectionStore",
    "ColQwenNativeEncoder",
    "DereferenceStatus",
    "EmbeddingDereferenceResult",
    "EmbeddingProfileMismatch",
    "EmbeddingProtocolError",
    "EmbeddingReferenceDereferencer",
    "EmbeddingReferenceResolver",
    "EmbeddingServiceError",
    "EmbeddingServiceSettings",
    "EmbeddingServiceUnavailable",
    "EmbeddingSet",
    "EvidenceClosureResolver",
    "EvidenceClosureValidator",
    "EvidenceEvent",
    "EvidencePack",
    "EvidencePackReference",
    "EvidenceSubscription",
    "FakeMultimodalEncoder",
    "FeedState",
    "GroundingComposition",
    "GroundingValidationError",
    "HigherOrderGrounding",
    "InMemoryMultimodalProjectionStore",
    "LocalFileAssetResolver",
    "MappingAssetResolver",
    "MultimodalEmbeddingProfile",
    "MultimodalEncoder",
    "MultimodalImageQueryEncoder",
    "MultimodalProjectionStore",
    "MultimodalRecallTiming",
    "MultimodalRetrievalSidecar",
    "MultimodalSearchHit",
    "MultimodalSourceBundle",
    "MultimodalSourceUnit",
    "PgVectorMultimodalProjectionStore",
    "PinnedEntityRef",
    "ProjectionIntegrityError",
    "RemoteMultimodalEncoder",
    "ResolvedEntityGrounding",
    "SQLiteMultimodalProjectionStore",
    "SourceEvidenceRef",
    "SynchronousMultimodalRecall",
    "TinyEmbeddingFunction",
    "VllmEmbeddingSettings",
    "VllmMultimodalEncoder",
    "audio_interval_unit",
    "build_source_bundle",
    "embed_pending",
    "embedding_profile",
    "manifest_units",
    "pdf_manifest_units",
    "resolve_embedding_function",
    "resolve_embedding_functions",
    "score_embedding_sets",
    "split_text_units",
    "synchronous_multimodal_recall",
    "to_core_embedding_profile",
    "validate_shared_postgres_embedding_profile",
    "video_interval_unit",
    "video_region_track_unit",
    "webpage_units",
]


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
