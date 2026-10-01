from __future__ import annotations

import sys
from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from llm_wiki_embedding_contract import EmbeddingProfile
from llm_wiki_embedding_service.app import create_app
from llm_wiki_embedding_service.clip_encoder import CLIPDualProjectionEncoder
from llm_wiki_embedding_service.config import (
    BGE_QUERY_INSTRUCTION,
    BGE_SMALL_DIMENSION,
    BGE_SMALL_MODEL,
    BGE_SMALL_REVISION,
    CLIP_DIMENSION,
    CLIP_MODEL,
    CLIP_MODEL_SHA256,
    CLIP_REVISION,
    EmbeddingServiceConfig,
    load_config,
)


class _FakeEncoder:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile

    def encode(self, items):
        return [((1.0,) + (0.0,) * (self.profile.dimension - 1),) for _ in items]


def test_fastapi_service_health_capabilities_and_authenticated_embedding() -> None:
    config = EmbeddingServiceConfig(
        dimension=64, revision="test-revision", token="secret", batch_size=8
    )
    with TestClient(create_app(encoder=_FakeEncoder(config.profile), config=config)) as client:
        assert client.get("/healthz").json()["ok"] is True
        assert client.get("/readyz").status_code == 200
        assert client.get("/v1/capabilities").status_code == 401
        capabilities = client.get(
            "/v1/capabilities", headers={"Authorization": "Bearer secret"}
        )
        assert capabilities.json()["profile"]["fingerprint"] == config.profile.fingerprint
        assert capabilities.json()["batch_size"] == 8
        response = client.post(
            "/v1/represent",
            headers={"Authorization": "Bearer secret"},
            json={
                "contract_version": "v1",
                "request_id": "q1",
                "operation": "query",
                "profile_fingerprint": config.profile.fingerprint,
                "items": [{"item_id": "q", "modality": "text", "text": "hello"}],
            },
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["item_id"] == "q"


def test_embedding_config_uses_supplied_environment_mapping() -> None:
    config = load_config(
        {
            "LLM_WIKI_EMBEDDING_MODEL": "test/qwen-vl",
            "LLM_WIKI_EMBEDDING_MODEL_REVISION": "test-revision",
            "LLM_WIKI_EMBEDDING_DIMENSION": "1536",
            "LLM_WIKI_EMBEDDING_DEVICE": "cpu",
            "LLM_WIKI_EMBEDDING_TORCH_BACKEND": "cpu",
            "LLM_WIKI_EMBEDDING_MAX_ITEMS": "7",
            "LLM_WIKI_EMBEDDING_MAX_REQUEST_BYTES": "12345",
            "LLM_WIKI_EMBEDDING_INSTRUCTION": "custom instruction",
        }
    )
    assert config.model == "test/qwen-vl"
    assert config.dimension == 1536
    assert config.max_items == 7
    assert config.max_request_bytes == 12345
    assert config.instruction == "custom instruction"


def test_clip_config_uses_pinned_dual_projection_profile_and_local_weights_path() -> None:
    config = load_config(
        {
            "LLM_WIKI_EMBEDDING_ENCODER": "clip-vit-b32",
            "LLM_WIKI_EMBEDDING_MODEL_REVISION": CLIP_REVISION,
            "LLM_WIKI_EMBEDDING_MODEL_PATH": "D:/models/clip",
        }
    )

    assert config.model == CLIP_MODEL
    assert config.model_path == "D:/models/clip"
    assert config.dimension == CLIP_DIMENSION
    assert config.profile.embedding == "dense"
    assert config.profile.max_sequence_length == 77
    assert config.profile.preprocessing_fingerprint == "clip-vit-b32:shared-text-image-projections-v1"
    assert len(CLIP_MODEL_SHA256) == 64


def test_clip_config_rejects_a_dimension_that_does_not_match_its_projection() -> None:
    with pytest.raises(ValueError, match="projection dimension"):
        EmbeddingServiceConfig(
            encoder="clip-vit-b32",
            revision=CLIP_REVISION,
            dimension=384,
        )


def test_bge_small_config_is_pinned_and_isolated_as_384d_text_profile() -> None:
    config = load_config({"LLM_WIKI_EMBEDDING_ENCODER": "bge-small-en-v1.5"})

    assert config.model == BGE_SMALL_MODEL
    assert config.revision == BGE_SMALL_REVISION
    assert config.dimension == BGE_SMALL_DIMENSION
    assert config.device == "cpu"
    assert config.profile.max_sequence_length == 512
    assert "cls-l2" in config.profile.preprocessing_fingerprint
    assert "query-prefix-v1" in config.profile.preprocessing_fingerprint
    assert config.profile.fingerprint != load_config(
        {
            "LLM_WIKI_EMBEDDING_ENCODER": "clip-vit-b32",
            "LLM_WIKI_EMBEDDING_MODEL_REVISION": CLIP_REVISION,
        }
    ).profile.fingerprint


def test_bge_small_uses_cls_pooling_normalization_and_query_instruction() -> None:
    torch = pytest.importorskip("torch")
    from llm_wiki_embedding_service.encoder import BgeSmallTextEncoder

    config = load_config({"LLM_WIKI_EMBEDDING_ENCODER": "bge-small-en-v1.5"})
    calls: list[list[str]] = []

    class _Tokenizer:
        def __call__(self, texts, **kwargs):
            calls.append(list(texts))
            assert kwargs["max_length"] == 512
            return {"input_ids": torch.ones((len(texts), 2), dtype=torch.long)}

    class _Model:
        def __call__(self, **inputs):
            rows = inputs["input_ids"].shape[0]
            hidden = torch.zeros((rows, 2, BGE_SMALL_DIMENSION))
            hidden[:, 0, 0] = 3.0
            hidden[:, 0, 1] = 4.0
            hidden[:, 1, 2] = 50.0
            return SimpleNamespace(last_hidden_state=hidden)

    encoder = BgeSmallTextEncoder(
        _Model(), _Tokenizer(), profile=config.profile, device="cpu"
    )
    vectors = encoder.encode(
        [
            {"text": "AMD earnings", "operation": "query"},
            {"text": "AMD earnings", "operation": "document"},
        ]
    )

    assert calls == [[BGE_QUERY_INSTRUCTION + "AMD earnings", "AMD earnings"]]
    assert len(vectors) == 2
    assert vectors[0][0][:3] == pytest.approx((0.6, 0.8, 0.0))
    assert vectors[1][0][:3] == pytest.approx((0.6, 0.8, 0.0))
    assert all(len(item[0]) == BGE_SMALL_DIMENSION for item in vectors)


def test_clip_encodes_text_and_image_through_their_shared_projection_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_module = pytest.importorskip("PIL.Image")
    config = load_config(
        {
            "LLM_WIKI_EMBEDDING_ENCODER": "clip-vit-b32",
            "LLM_WIKI_EMBEDDING_MODEL_REVISION": CLIP_REVISION,
        }
    )
    calls: list[str] = []

    class _Tensor:
        ndim = 2
        shape = (1, CLIP_DIMENSION)

        def __init__(self, row: list[float]) -> None:
            self._row = row

        def float(self):
            return self

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return [self._row]

    class _FakeModel:
        def get_text_features(self, **_inputs):
            calls.append("text")
            projected = _Tensor([1.0, *([0.0] * (CLIP_DIMENSION - 1))])
            return SimpleNamespace(pooler_output=projected)

        def get_image_features(self, **_inputs):
            calls.append("image")
            return _Tensor([0.0, 1.0, *([0.0] * (CLIP_DIMENSION - 2))])

    class _Functional:
        @staticmethod
        def normalize(values, *, p: int, dim: int):
            assert (p, dim) == (2, -1)
            return values

    fake_torch = SimpleNamespace(
        inference_mode=nullcontext,
        nn=SimpleNamespace(functional=_Functional),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    encoder = CLIPDualProjectionEncoder(
        _FakeModel(),
        lambda **_inputs: {},
        profile=config.profile,
        device="cpu",
    )

    image_buffer = BytesIO()
    image_module.new("RGB", (2, 2), color="white").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    vectors = encoder.encode(
        [
            {"text": "rabbit"},
            {"asset": {"bytes": image_bytes}},
            {"text": "rabbit", "asset": {"bytes": image_bytes}},
        ]
    )

    assert calls == ["text", "text", "image", "image"]
    assert len(vectors) == 3
    assert all(len(item[0]) == CLIP_DIMENSION for item in vectors)
    assert vectors[0][0][:2] == (1.0, 0.0)
    assert vectors[1][0][:2] == (0.0, 1.0)
    assert vectors[2][0][:2] == pytest.approx((2**-0.5, 2**-0.5))
