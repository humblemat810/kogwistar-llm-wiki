from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from kogwistar_llm_wiki.multimodal_projection import (
    MultimodalEmbeddingProfile,
    MultimodalSourceUnit,
    ProjectionIntegrityError,
)
from kogwistar_llm_wiki.multimodal_remote import (
    RemoteMultimodalEncoder,
    RepresentationProtocolError,
    RepresentationServiceSettings,
    RepresentationServiceUnavailable,
)
from kogwistar_llm_wiki.representation_service.app import _represent_payload
from kogwistar_llm_wiki.representation_service.config import RepresentationServiceConfig


def _profile() -> MultimodalEmbeddingProfile:
    return RepresentationServiceConfig(dimension=64).profile


class _Response:
    status = 200

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FakeEncoder:
    def __init__(self, profile: MultimodalEmbeddingProfile) -> None:
        self.profile = profile

    def encode_queries(self, queries, *, batch_size=None):
        del batch_size
        return [((1.0,) + (0.0,) * 63,) for _ in queries]

    def encode_image_queries(self, images, *, batch_size=None):
        del batch_size
        return [((0.0, 1.0) + (0.0,) * 62,) for _ in images]

    def encode_documents(self, units, *, batch_size=None, resolver=None):
        del batch_size
        return [((0.0, 0.0, 1.0) + (0.0,) * 61,) for _ in units]


def test_remote_adapter_sends_resolved_bytes_and_preserves_order() -> None:
    profile = _profile()
    captured: list[dict[str, object]] = []

    def opener(request, *, timeout):
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        captured.append(body)
        return _Response(
            {
                "contract_version": "v1",
                "request_id": body["request_id"],
                "profile": profile.canonical_payload(),
                "results": [
                    {"item_id": item["item_id"], "vectors": [[1.0] + [0.0] * 63]}
                    for item in body["items"]
                ],
            }
        )

    encoder = RemoteMultimodalEncoder(
        profile,
        RepresentationServiceSettings("http://representation:8790"),
        opener=opener,
    )
    unit = MultimodalSourceUnit(
        view_id="view-1",
        workspace_id="w",
        source_id="s",
        source_revision_id="r",
        modality="image",
        locator={"kind": "image"},
        content_ref="blob://one",
    )
    result = encoder.encode_documents(
        [unit],
        resolver=SimpleNamespace(resolve=lambda _: b"image-bytes"),
    )
    assert result == [((1.0,) + (0.0,) * 63,)]
    item = captured[0]["items"][0]
    assert "content_ref" not in item
    assert item["asset"]["sha256"]
    assert "blob://one" not in json.dumps(captured[0])


def test_remote_adapter_rejects_profile_or_order_mismatch() -> None:
    profile = _profile()

    def opener(request, *, timeout):
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        return _Response(
            {
                "contract_version": "v1",
                "request_id": body["request_id"],
                "profile": {**profile.canonical_payload(), "dimension": 8},
                "results": [],
            }
        )

    encoder = RemoteMultimodalEncoder(
        profile,
        RepresentationServiceSettings("http://representation:8790"),
        opener=opener,
    )
    with pytest.raises(RepresentationProtocolError, match="profile fingerprint"):
        encoder.encode_queries(["question"])


def test_remote_adapter_rejects_path_assets_and_hash_mismatch() -> None:
    profile = _profile()
    encoder = RemoteMultimodalEncoder(
        profile,
        RepresentationServiceSettings("http://representation:8790"),
        opener=lambda *_args, **_kwargs: pytest.fail("request must not be sent"),
    )
    unit = MultimodalSourceUnit(
        view_id="view-1", workspace_id="w", source_id="s", source_revision_id="r",
        modality="image", locator={}, content_ref="/private/image.png", asset_sha256="bad",
    )
    with pytest.raises(ProjectionIntegrityError):
        encoder.encode_documents([unit], resolver=SimpleNamespace(resolve=lambda _: io.BytesIO(b"x")))


def test_remote_adapter_exposes_outage_as_retryable_without_local_fallback() -> None:
    profile = _profile()

    def opener(*_args, **_kwargs):
        raise URLError("sidecar is down")

    encoder = RemoteMultimodalEncoder(
        profile,
        RepresentationServiceSettings("http://representation:8790"),
        opener=opener,
    )
    with pytest.raises(RepresentationServiceUnavailable, match="unavailable"):
        encoder.encode_queries(["question"])


def test_remote_service_host_allowlist_is_enforced() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        RepresentationServiceSettings(
            "https://remote.example/v1",
            allowed_hosts=("representation.internal",),
        )


def test_fake_service_contract_handles_document_and_query() -> None:
    config = RepresentationServiceConfig(dimension=64)
    encoder = _FakeEncoder(config.profile)
    query = _represent_payload(
        {
            "contract_version": "v1",
            "request_id": "q1",
            "operation": "query",
            "profile_fingerprint": config.profile.fingerprint,
            "items": [{"item_id": "q", "modality": "text", "text": "hello"}],
        },
        encoder,
        config,
    )
    document = _represent_payload(
        {
            "contract_version": "v1",
            "request_id": "d1",
            "operation": "document",
            "profile_fingerprint": config.profile.fingerprint,
            "items": [{"item_id": "d", "modality": "text", "text": "world"}],
        },
        encoder,
        config,
    )
    assert query["results"][0]["item_id"] == "q"
    assert document["results"][0]["item_id"] == "d"


def test_service_supports_mixed_query_and_preserves_item_order() -> None:
    config = RepresentationServiceConfig(dimension=64)
    result = _represent_payload(
        {
            "contract_version": "v1",
            "request_id": "q1",
            "operation": "query",
            "profile_fingerprint": config.profile.fingerprint,
            "items": [
                {"item_id": "t", "modality": "text", "text": "hello"},
                {
                    "item_id": "i",
                    "modality": "image",
                    "asset": {
                        "content_type": "image/png",
                        "sha256": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
                        "data_base64": "eA==",
                    },
                },
                {
                    "item_id": "m",
                    "modality": "image",
                    "text": "caption",
                    "asset": {
                        "content_type": "image/png",
                        "sha256": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
                        "data_base64": "eA==",
                    },
                },
            ],
        },
        _FakeEncoder(config.profile),
        config,
    )
    assert [item["item_id"] for item in result["results"]] == ["t", "i", "m"]
