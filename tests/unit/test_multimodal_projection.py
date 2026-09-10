from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from io import BytesIO
import json
import sys
from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.multimodal_projection import (
    ChromaMultimodalProjectionStore,
    EmbeddingProfileMismatch,
    FakeMultimodalEncoder,
    InMemoryMultimodalProjectionStore,
    ColQwenNativeEncoder,
    MultimodalEmbeddingProfile,
    MultimodalSourceUnit,
    ProjectionIntegrityError,
    Qwen3VLDenseEncoder,
    QWEN3_VL_MAX_DIMENSION,
    QWEN3_VL_MIN_DIMENSION,
    SQLiteMultimodalProjectionStore,
    embed_pending,
    score_embedding_sets,
)
from kogwistar_llm_wiki.multimodal_sources import (
    MappingAssetResolver,
    build_source_bundle,
    manifest_units,
)
from scripts.pull_colqwen_model import verify_checkpoint
from scripts.pull_qwen3_vl_model import verify_checkpoint as verify_qwen3_vl_checkpoint
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline, build_in_memory_namespace_engines


def _profile(**overrides: object) -> MultimodalEmbeddingProfile:
    values: dict[str, object] = {
        "provider": "fake",
        "model": "colqwen-compatible-fixture",
        "representation": "late_interaction",
        "dimension": 2,
    }
    values.update(overrides)
    return MultimodalEmbeddingProfile(**values)  # type: ignore[arg-type]


def _unit(view_id: str, text: str) -> MultimodalSourceUnit:
    return MultimodalSourceUnit(
        view_id=view_id,
        workspace_id="demo",
        source_id="article-1",
        source_revision_id="rev-1",
        modality="text",
        locator={"start_char": 0, "end_char": len(text)},
        text=text,
    )


def test_profile_fingerprint_captures_semantic_and_preprocessing_identity() -> None:
    profile = _profile()
    assert profile.fingerprint == _profile().fingerprint
    assert profile.fingerprint != _profile(model="other-model").fingerprint
    assert profile.fingerprint != _profile(max_image_patches=512).fingerprint
    assert profile.fingerprint != _profile(metric="cosine").fingerprint


def test_late_interaction_uses_colbert_maxsim() -> None:
    profile = _profile()
    score = score_embedding_sets(
        ((1.0, 0.0), (0.0, 1.0)),
        ((1.0, 0.0), (0.0, 1.0)),
        profile=profile,
    )
    assert score == pytest.approx(2.0)


def test_stage_one_then_stage_two_search_is_provider_free() -> None:
    encoder = FakeMultimodalEncoder(profile=_profile(dimension=8))
    store = InMemoryMultimodalProjectionStore(scope="demo:multimodal", profile=encoder.profile)
    first = _unit("view-1", "reinforcement learning rewards")
    second = _unit("view-2", "database indexing and vectors")
    store.capture(first)
    store.capture(second)
    assert store.stage_counts() == {"stage1": 2, "stage2": 0, "pending_stage2": 2}

    assert embed_pending(store, encoder, batch_size=2) == 2
    assert store.stage_counts() == {"stage1": 2, "stage2": 2, "pending_stage2": 0}
    hits = store.search(encoder.encode_queries(["reinforcement learning"])[0], profile=encoder.profile)
    assert [hit.view_id for hit in hits] == ["view-1", "view-2"]
    assert hits[0].source_revision_id == "rev-1"


def test_image_query_uses_native_image_capability_without_captioning() -> None:
    profile = _profile(dimension=8)
    encoder = FakeMultimodalEncoder(profile=profile)
    store = InMemoryMultimodalProjectionStore(scope="demo:multimodal", profile=profile)
    image = MultimodalSourceUnit(
        view_id="image-view",
        workspace_id="demo",
        source_id="image-source",
        source_revision_id="rev-1",
        modality="image",
        locator={"kind": "whole_image"},
        content_ref="blob://image-1",
    )
    store.capture(image)
    assert embed_pending(store, encoder) == 1
    query_vectors = encoder.encode_image_queries(["blob://image-1"])
    hit = store.search(query_vectors[0], profile=profile, limit=1)[0]
    assert hit.view_id == "image-view"


def test_profile_mismatch_fails_before_projection_write() -> None:
    profile = _profile(dimension=2)
    store = InMemoryMultimodalProjectionStore(scope="demo:multimodal", profile=profile)
    unit = _unit("view-1", "text")
    store.capture(unit)
    with pytest.raises(EmbeddingProfileMismatch):
        store.upsert_embedding(unit, ((1.0, 0.0),), profile=_profile(model="changed"))
    assert store.stage_counts() == {"stage1": 1, "stage2": 0, "pending_stage2": 1}


def test_single_vector_rejects_multiple_vectors() -> None:
    profile = _profile(representation="single_vector")
    store = InMemoryMultimodalProjectionStore(scope="demo:pooled", profile=profile)
    with pytest.raises(ProjectionIntegrityError, match="single_vector"):
        store.upsert_embedding(_unit("view-1", "text"), ((1.0, 0.0), (0.0, 1.0)), profile=profile)


def test_sqlite_stage_progress_survives_reopen(tmp_path) -> None:
    profile = _profile(dimension=8)
    path = tmp_path / "multimodal.sqlite"
    encoder = FakeMultimodalEncoder(profile=profile)
    first = SQLiteMultimodalProjectionStore(path, scope="demo:multimodal", profile=profile)
    first.capture(_unit("view-1", "page with a chart"))
    first.close()

    resumed = SQLiteMultimodalProjectionStore(path, scope="demo:multimodal", profile=profile)
    assert resumed.stage_counts() == {"stage1": 1, "stage2": 0, "pending_stage2": 1}
    assert embed_pending(resumed, encoder) == 1
    resumed.close()

    reopened = SQLiteMultimodalProjectionStore(path, scope="demo:multimodal", profile=profile)
    assert reopened.stage_counts() == {"stage1": 1, "stage2": 1, "pending_stage2": 0}
    reopened.close()


def test_sqlite_profile_mismatch_is_rejected_on_open(tmp_path) -> None:
    path = tmp_path / "multimodal.sqlite"
    SQLiteMultimodalProjectionStore(path, scope="demo:multimodal", profile=_profile(dimension=2)).close()
    with pytest.raises(EmbeddingProfileMismatch):
        SQLiteMultimodalProjectionStore(path, scope="demo:multimodal", profile=_profile(dimension=3))


def test_chroma_projection_persists_stage_two_late_interaction(tmp_path) -> None:
    if importlib.util.find_spec("chromadb") is None:
        pytest.skip("chromadb is not installed")
    profile = _profile(dimension=8)
    encoder = FakeMultimodalEncoder(profile=profile)
    store = ChromaMultimodalProjectionStore(
        tmp_path / "chroma", scope="demo:multimodal", profile=profile
    )
    store.capture(_unit("view-1", "visual chart explanation"))
    assert store.stage_counts() == {"stage1": 1, "stage2": 0, "pending_stage2": 1}
    assert embed_pending(store, encoder) == 1
    assert store.stage_counts() == {"stage1": 1, "stage2": 1, "pending_stage2": 0}
    hit = store.search(encoder.encode_queries(["visual chart"])[0], profile=profile, limit=1)[0]
    assert hit.view_id == "view-1"
    store.close()

    reopened = ChromaMultimodalProjectionStore(
        tmp_path / "chroma", scope="demo:multimodal", profile=profile
    )
    assert reopened.stage_counts()["stage2"] == 1
    assert reopened.search(encoder.encode_queries(["visual chart"])[0], profile=profile, limit=1)[0].view_id == "view-1"
    reopened.max_search_vectors = 1
    with pytest.raises(ProjectionIntegrityError, match="exceeds configured bound"):
        reopened.search(encoder.encode_queries(["visual chart"])[0], profile=profile, limit=1)
    reopened.close()


def test_embedding_output_count_must_match_captured_units() -> None:
    class BadEncoder(FakeMultimodalEncoder):
        def encode_documents(self, units, *, batch_size=None, resolver=None):
            del units, batch_size, resolver
            return []

    encoder = BadEncoder(profile=_profile(dimension=8))
    store = InMemoryMultimodalProjectionStore(scope="demo:multimodal", profile=encoder.profile)
    store.capture(_unit("view-1", "text"))
    with pytest.raises(ProjectionIntegrityError, match="different number"):
        embed_pending(store, encoder)


def test_colqwen_adapter_keeps_heavy_dependencies_optional() -> None:
    if importlib.util.find_spec("transformers") is not None:
        pytest.skip("optional ColQwen dependencies are installed in this environment")
    with pytest.raises(RuntimeError, match="optional multimodal dependencies"):
        ColQwenNativeEncoder.from_pretrained()


def test_qwen3_vl_dense_profile_and_dimensions_are_explicit() -> None:
    profile = _profile(
        provider="transformers",
        model="Qwen/Qwen3-VL-Embedding-2B",
        representation="dense",
        dimension=1024,
    )
    assert profile.representation == "dense"
    assert QWEN3_VL_MIN_DIMENSION == 64
    assert QWEN3_VL_MAX_DIMENSION == 2048
    assert profile.fingerprint != _profile(
        provider="ollama", model="qwen3-embedding:0.6b", representation="single_vector", dimension=1024
    ).fingerprint


def test_qwen3_vl_dense_fake_model_preserves_mixed_batch_order(monkeypatch) -> None:
    class FakeTorch:
        @contextmanager
        def inference_mode(self):
            yield

    class FakeProcessor:
        def apply_chat_template(self, conversations, **kwargs):
            del kwargs
            return [
                "|".join(str(part.get("text", "")) for message in conversation for part in message["content"])
                for conversation in conversations
            ]

        def __call__(self, *, text, **kwargs):
            del kwargs
            return {"text": text}

    class FakeModel:
        def __call__(self, *, text):
            return SimpleNamespace(
                last_hidden_state=[
                    [float(index + 1)] + [0.0] * 63
                    for index, _ in enumerate(text)
                ]
            )

    # The fake model emits the lightweight list branch; no real model or
    # tokenizer is needed to verify ordering and one-vector-per-unit semantics.
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    profile = _profile(
        provider="transformers",
        model="Qwen/Qwen3-VL-Embedding-2B",
        representation="dense",
        dimension=64,
    )
    encoder = Qwen3VLDenseEncoder(
        FakeModel(), FakeProcessor(), profile=profile, device="cpu", batch_size=2
    )
    units = (
        _unit("dense-1", "first"),
        _unit("dense-2", "second"),
        _unit("dense-3", "third"),
    )
    result = encoder.encode_documents(units)
    assert len(result) == 3
    assert all(len(embedding) == 1 and len(embedding[0]) == 64 for embedding in result)
    assert result[0][0][0] == pytest.approx(1.0)
    assert result[1][0][0] == pytest.approx(1.0)


def test_qwen3_vl_decodes_transport_asset_bytes_before_vision_processing(monkeypatch) -> None:
    from io import BytesIO

    from PIL import Image

    class FakeTorch:
        @contextmanager
        def inference_mode(self):
            yield

    class FakeProcessor:
        def __init__(self):
            self.images = None
            self.video_kwargs = None

        def apply_chat_template(self, conversations, **kwargs):
            del conversations, kwargs
            return ["image"]

        def __call__(self, **kwargs):
            self.images = kwargs.get("images")
            self.video_kwargs = kwargs.get("do_sample_frames")
            return {}

    class FakeModel:
        def __call__(self):
            return SimpleNamespace(last_hidden_state=[[1.0] + [0.0] * 63])

    observed: list[object] = []

    def fake_vision(conversations, **kwargs):
        del kwargs
        observed.append(conversations[0][1]["content"][0]["image"])
        return [observed[0]], None, {"do_sample_frames": False}

    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), color="white").save(image_bytes, format="PNG")
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    profile = _profile(
        provider="transformers",
        model="Qwen/Qwen3-VL-Embedding-2B",
        representation="dense",
        dimension=64,
    )
    processor = FakeProcessor()
    encoder = Qwen3VLDenseEncoder(
        FakeModel(),
        processor,
        profile=profile,
        device="cpu",
        vision_processor=fake_vision,
    )
    unit = MultimodalSourceUnit(
        view_id="image",
        workspace_id="workspace",
        source_id="source",
        source_revision_id="revision",
        modality="image",
        locator={"kind": "whole_image"},
        content_ref="asset://image",
    )

    result = encoder.encode_documents(
        [unit], resolver=MappingAssetResolver({"asset://image": image_bytes.getvalue()})
    )

    assert len(result) == 1
    assert observed and isinstance(observed[0], Image.Image)
    assert processor.images == observed
    assert processor.video_kwargs is False


@pytest.mark.parametrize("dimension", [63, 2049])
def test_qwen3_vl_rejects_dimensions_outside_mrl_range(dimension: int) -> None:
    profile = _profile(
        provider="transformers",
        model="Qwen/Qwen3-VL-Embedding-2B",
        representation="dense",
        dimension=dimension,
    )
    with pytest.raises(ValueError, match="between 64 and 2048"):
        Qwen3VLDenseEncoder(object(), object(), profile=profile, device="cpu")


def test_colqwen_adapter_resolves_external_bytes_and_enforces_patch_cap(monkeypatch) -> None:
    from PIL import Image

    class FakeTorch:
        @contextmanager
        def inference_mode(self):
            yield

    class FakeProcessor:
        def __init__(self):
            self.max_pixels = None
            self.images = None

        def __call__(self, *, images, return_tensors, max_pixels):
            self.images = images
            self.max_pixels = max_pixels
            assert return_tensors == "pt"
            return {}

    class FakeModel:
        def __call__(self, **kwargs):
            assert kwargs == {}
            return SimpleNamespace(embeddings=[[(1.0, 0.0), (0.0, 1.0)]])

    image_buffer = BytesIO()
    Image.new("RGB", (4, 4), color="white").save(image_buffer, format="PNG")
    profile = _profile(max_image_patches=10)
    processor = FakeProcessor()
    encoder = ColQwenNativeEncoder(
        FakeModel(), processor, profile=profile, device="cpu", batch_size=1
    )
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    unit = MultimodalSourceUnit(
        view_id="image-1",
        workspace_id="demo",
        source_id="source-1",
        source_revision_id="rev-1",
        modality="image",
        locator={"kind": "whole_image"},
        content_ref="blob://image-1",
    )
    result = encoder.encode_documents(
        [unit], resolver=MappingAssetResolver({"blob://image-1": image_buffer.getvalue()})
    )
    assert len(result) == 1
    assert len(result[0]) == 2
    assert processor.max_pixels == 28 * 28 * 10
    assert processor.images[0].size == (4, 4)


def test_colqwen_adapter_routes_mixed_text_and_image_views_in_order(monkeypatch) -> None:
    from PIL import Image

    class FakeTorch:
        @contextmanager
        def inference_mode(self):
            yield

    class MixedProcessor:
        def __init__(self):
            self.calls = []

        def __call__(self, *, text=None, images=None, return_tensors, **kwargs):
            del kwargs, return_tensors
            if text is not None:
                self.calls.append(("text", tuple(text)))
            else:
                self.calls.append(("image", len(images)))
            return {}

    class FakeModel:
        def __call__(self, **kwargs):
            assert kwargs == {}
            return SimpleNamespace(embeddings=[[(1.0, 0.0), (0.0, 1.0)]])

    image_buffer = BytesIO()
    Image.new("RGB", (4, 4), color="white").save(image_buffer, format="PNG")
    processor = MixedProcessor()
    encoder = ColQwenNativeEncoder(
        FakeModel(), processor, profile=_profile(), device="cpu", batch_size=2
    )
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    units = (
        MultimodalSourceUnit(
            view_id="text-1", workspace_id="demo", source_id="source-1", source_revision_id="rev-1",
            modality="text", locator={"kind": "text_span"}, text="text one",
        ),
        MultimodalSourceUnit(
            view_id="image-1", workspace_id="demo", source_id="source-1", source_revision_id="rev-1",
            modality="image", locator={"kind": "whole_image"}, content_ref="blob://image-1",
        ),
        MultimodalSourceUnit(
            view_id="text-2", workspace_id="demo", source_id="source-1", source_revision_id="rev-1",
            modality="table", locator={"kind": "table"}, text="table text",
        ),
    )
    result = encoder.encode_documents(
        units, resolver=MappingAssetResolver({"blob://image-1": image_buffer.getvalue()})
    )
    assert len(result) == 3
    assert [kind for kind, _ in processor.calls] == ["text", "image", "text"]


def test_colqwen_checkpoint_verifier_rejects_partial_and_accepts_complete(tmp_path) -> None:
    with pytest.raises(ValueError, match="missing required files"):
        verify_checkpoint(tmp_path)
    for name in ("config.json", "preprocessor_config.json"):
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "architectures": ["ColQwen2ForRetrieval"],
                    "embedding_dim": 128,
                }
            ),
            encoding="utf-8",
        )
    (tmp_path / "model.safetensors").write_bytes(b"fixture")
    (tmp_path / "weights.incomplete").write_bytes(b"partial")
    with pytest.raises(ValueError, match="incomplete"):
        verify_checkpoint(tmp_path)
    (tmp_path / "weights.incomplete").unlink()
    result = verify_checkpoint(tmp_path)
    assert result["embedding_dim"] == 128
    assert result["model_bytes"] == 7


def test_qwen3_vl_checkpoint_verifier_rejects_partial_and_accepts_complete(tmp_path) -> None:
    with pytest.raises(ValueError, match="config.json"):
        verify_qwen3_vl_checkpoint(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "qwen3_vl", "architectures": ["Qwen3VLForEmbedding"]}),
        encoding="utf-8",
    )
    (tmp_path / "model.safetensors").write_bytes(b"fixture")
    result = verify_qwen3_vl_checkpoint(tmp_path)
    assert result["model_type"] == "qwen3_vl"
    assert result["model_bytes"] == 7


def test_ingest_pipeline_delegates_opt_in_multimodal_flow(tmp_path) -> None:
    profile = _profile(dimension=8)
    encoder = FakeMultimodalEncoder(profile=profile)
    store = SQLiteMultimodalProjectionStore(tmp_path / "projection.sqlite", scope="demo", profile=profile)
    engines = build_in_memory_namespace_engines(tmp_path / "graph")
    pipeline = IngestPipeline(
        engines,
        multimodal_projection_store=store,
        multimodal_encoder=encoder,
    )
    try:
        assert pipeline.capture_multimodal_units([_unit("view-1", "a multimodal article")]) == 1
        assert pipeline.embed_multimodal_pending() == 1
        assert pipeline.search_multimodal("multimodal article", limit=1)[0].view_id == "view-1"
        bundle = pipeline.capture_multimodal_source(
            workspace_id="demo",
            source_id="page-1",
            source_revision_id="rev-1",
            source_format="html",
            source_uri="https://example.test/page",
            raw_text="<p>another source</p><img src='blob://image'>",
        )
        assert len(bundle.units) == 2
        assert store.stage_counts()["stage1"] == 3
        image_unit = MultimodalSourceUnit(
            view_id="standalone-image",
            workspace_id="demo",
            source_id="image-source",
            source_revision_id="rev-1",
            modality="image",
            locator={"kind": "whole_image"},
            content_ref="blob://image-query",
        )
        pipeline.capture_multimodal_units([image_unit])
        assert pipeline.embed_multimodal_pending() == 3
        assert pipeline.search_multimodal_image(["blob://image-query"], limit=1)[0].view_id == "standalone-image"
        mixed = pipeline.search_multimodal_mixed(
            text_queries=("multimodal article",),
            images=("blob://image-query",),
            limit=2,
        )
        assert mixed
        assert mixed[0].metadata["query_fusion"] == "weighted_mean_of_bounded_independent_queries"
        assert mixed[0].metadata["query_contributions"]
    finally:
        store.close()
        engines.close()


def test_source_bundle_splits_text_into_half_open_revision_bound_units() -> None:
    bundle = build_source_bundle(
        workspace_id="demo",
        source_id="article-1",
        source_revision_id="rev-1",
        source_format="markdown",
        raw_text="first line\nsecond line\nthird line",
        max_chars=12,
    )
    assert [unit.text for unit in bundle.units] == ["first line", "\nsecond line", "\nthird line"]
    for unit in bundle.units:
        start = int(unit.locator["start_char"])
        end = int(unit.locator["end_char"])
        assert unit.text == "first line\nsecond line\nthird line"[start:end]
        assert unit.locator["end_semantics"] == "exclusive"
    assert len({unit.view_id for unit in bundle.units}) == 3
    restored = type(bundle).from_payload(bundle.to_payload())
    assert restored.to_payload() == bundle.to_payload()


def test_webpage_bundle_keeps_visible_text_images_and_tables_separate() -> None:
    html = """
    <html><body><h1>Graph learning</h1>
    <script>do_not_index()</script>
    <img src="s3://assets/chart.png" alt="reward curve">
    <table><tr><td>epoch</td><td>loss</td></tr></table>
    </body></html>
    """
    bundle = build_source_bundle(
        workspace_id="demo",
        source_id="page-1",
        source_revision_id="rev-2",
        source_format="html",
        source_uri="https://example.test/article",
        raw_text=html,
    )
    assert [unit.modality for unit in bundle.units] == ["webpage", "image", "table"]
    assert "do_not_index" not in (bundle.units[0].text or "")
    assert bundle.units[1].content_ref == "s3://assets/chart.png"
    assert bundle.units[2].text == "epoch loss"
    assert MappingAssetResolver({"s3://assets/chart.png": b"fixture"}).resolve(bundle.units[1]) == b"fixture"


def test_pdf_manifest_keeps_page_images_tables_and_charts_separate() -> None:
    bundle = build_source_bundle(
        workspace_id="demo",
        source_id="paper-1",
        source_revision_id="rev-3",
        source_format="pdf",
        manifest={
            "pages": [
                {
                    "page_number": 4,
                    "text": "Results",
                    "images": [{"content_ref": "blob://figure-1", "metadata": {"caption": "plot"}}],
                    "tables": [{"text": "metric value"}],
                    "charts": [{"content_ref": "blob://chart-1"}],
                }
            ]
        },
    )
    assert [unit.modality for unit in bundle.units] == ["pdf_page", "image", "table", "chart"]
    assert bundle.units[0].locator == {"kind": "pdf_page", "page_number": 4}
    assert bundle.units[1].content_ref == "blob://figure-1"
    assert bundle.units[3].content_ref == "blob://chart-1"


def test_manifest_units_are_revision_bound_and_reingest_changes_view_ids() -> None:
    manifest = {"units": [{"modality": "image", "content_ref": "blob://one"}]}
    first = manifest_units(
        workspace_id="demo", source_id="source", revision_id="rev-1", manifest=manifest
    )
    second = manifest_units(
        workspace_id="demo", source_id="source", revision_id="rev-2", manifest=manifest
    )
    assert first[0].source_revision_id == "rev-1"
    assert first[0].view_id != second[0].view_id


def test_manifest_rejects_unit_without_text_or_external_reference() -> None:
    with pytest.raises(ValueError, match="content_ref or text"):
        manifest_units(
            workspace_id="demo",
            source_id="source",
            revision_id="rev-1",
            manifest={"units": [{"modality": "table"}]},
        )


def test_source_payload_rejects_malformed_units_instead_of_dropping_them() -> None:
    manifest = {"units": [{"modality": "text", "text": "ok"}, "bad"]}
    with pytest.raises(ValueError, match="must be mappings"):
        manifest_units(
            workspace_id="demo", source_id="source", revision_id="rev-1", manifest=manifest
        )

    bundle = build_source_bundle(
        workspace_id="demo",
        source_id="source",
        source_revision_id="rev-1",
        source_format="text",
        raw_text="hello",
    )
    payload = bundle.to_payload()
    payload["units"] = [payload["units"][0], "bad"]  # type: ignore[index]
    with pytest.raises(ValueError, match="must be mappings"):
        type(bundle).from_payload(payload)
