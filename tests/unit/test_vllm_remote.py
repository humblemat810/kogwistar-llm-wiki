from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.multimodal_projection import (
    MultimodalSourceUnit,
    ProjectionIntegrityError,
)
from kogwistar_llm_wiki.multimodal_remote import EmbeddingProtocolError
from kogwistar_llm_wiki.vllm_remote import VllmEmbeddingSettings, VllmMultimodalEncoder


IMAGE = b"small-image"
IMAGE_DIGEST = "@sha256:" + "a" * 64


class _Response:
    status = 200

    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload or {}).encode("utf-8")


def _settings() -> VllmEmbeddingSettings:
    return VllmEmbeddingSettings(
        url="http://embedding:8000",
        token="vllm-secret",
        image_digest=IMAGE_DIGEST,
        model_revision="qwen-revision",
        allowed_hosts=("embedding",),
    )


def test_vllm_profile_is_distinct_from_transformers_profile() -> None:
    settings = _settings()
    from llm_wiki_embedding_contract import EmbeddingProfile

    transformers = EmbeddingProfile(
        provider="transformers",
        model=settings.model,
        model_revision=settings.model_revision,
        embedding="dense",
        dimension=1024,
        metric="dot",
        preprocessing_fingerprint="qwen3-vl:transformers",
    )
    assert settings.profile.provider == "vllm"
    assert "pooling:embed" in settings.profile.preprocessing_fingerprint
    assert settings.profile.fingerprint != transformers.fingerprint


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"token": ""}, "token is required"),
        ({"model_revision": ""}, "model revision is required"),
        ({"model": "other-model"}, "supports only"),
        ({"image_digest": "vllm:latest"}, "pinned by an @sha256 digest"),
        ({"dimension": 1536}, "supports 1024 dimensions only"),
    ],
)
def test_vllm_settings_fail_closed(kwargs: dict[str, object], message: str) -> None:
    values = {
        "url": "http://embedding:8000",
        "token": "secret",
        "image_digest": IMAGE_DIGEST,
        "model_revision": "revision",
        "allowed_hosts": ("embedding",),
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        VllmEmbeddingSettings(**values)  # type: ignore[arg-type]


def test_vllm_adapter_uses_chat_embeddings_and_preserves_request_identity() -> None:
    requests: list[dict[str, object]] = []

    def opener(request, *, timeout):
        del timeout
        payload = json.loads(request.data.decode("utf-8"))
        requests.append(payload)
        vector = [0.0] * 1024
        vector[0] = 1.0
        return _Response({"model": "Qwen/Qwen3-VL-Embedding-2B", "data": [{"index": 0, "embedding": vector}]})

    encoder = VllmMultimodalEncoder(_settings(), opener=opener)
    unit = MultimodalSourceUnit(
        view_id="view-1",
        workspace_id="workspace",
        source_id="source",
        source_revision_id="revision",
        modality="image",
        locator={"kind": "image"},
        content_ref="secret://image",
        text="a test image",
    )
    result = encoder.encode_documents(
        [unit], resolver=SimpleNamespace(resolve=lambda _: IMAGE)
    )

    assert len(result) == 1
    assert len(result[0]) == 1
    assert len(result[0][0]) == 1024
    assert requests[0]["dimensions"] == 1024
    messages = requests[0]["messages"]
    assert isinstance(messages, list)
    assert "secret://image" not in json.dumps(requests[0])
    assert "a test image" in json.dumps(requests[0])
    assert requests[0]["continue_final_message"] is True


def test_vllm_adapter_rejects_asset_without_resolver() -> None:
    encoder = VllmMultimodalEncoder(
        _settings(), opener=lambda *_args, **_kwargs: pytest.fail("must not send")
    )
    unit = MultimodalSourceUnit(
        view_id="view-1",
        workspace_id="workspace",
        source_id="source",
        source_revision_id="revision",
        modality="image",
        locator={},
        content_ref="secret://image",
    )
    with pytest.raises(ProjectionIntegrityError, match="resolver"):
        encoder.encode_documents([unit])


def test_vllm_adapter_rejects_wrong_model_or_result_index() -> None:
    def opener(request, *, timeout):
        del request, timeout
        vector = [0.0] * 1024
        return _Response({"model": "wrong-model", "data": [{"index": 1, "embedding": vector}]})

    encoder = VllmMultimodalEncoder(_settings(), opener=opener)
    with pytest.raises(EmbeddingProtocolError, match="unexpected model identity"):
        encoder.encode_queries(["question"])


def test_vllm_readiness_probes_health_and_authenticated_model() -> None:
    paths: list[str] = []

    def opener(request, *, timeout):
        del timeout
        paths.append(request.full_url)
        if request.full_url.endswith("/health"):
            return _Response({})
        return _Response({"data": [{"id": "Qwen/Qwen3-VL-Embedding-2B"}]})

    result = VllmMultimodalEncoder(_settings(), opener=opener).readiness()

    assert result["ready"] is True
    assert paths == [
        "http://embedding:8000/health",
        "http://embedding:8000/v1/models",
    ]
