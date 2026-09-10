from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from kogwistar_llm_wiki.representation_service.app import create_app
from kogwistar_llm_wiki.representation_service.config import RepresentationServiceConfig
from kogwistar_llm_wiki.representation_service.config import load_config
from tests.unit.test_multimodal_remote import _FakeEncoder


def test_fastapi_service_health_capabilities_and_authenticated_representation() -> None:
    config = RepresentationServiceConfig(dimension=64, token="secret")
    with TestClient(create_app(encoder=_FakeEncoder(config.profile), config=config)) as client:
        assert client.get("/healthz").json()["ok"] is True
        assert client.get("/readyz").status_code == 200
        assert client.get("/v1/capabilities").status_code == 401
        capabilities = client.get(
            "/v1/capabilities", headers={"Authorization": "Bearer secret"}
        )
        assert capabilities.json()["profile"]["fingerprint"] == config.profile.fingerprint
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


def test_representation_config_uses_supplied_environment_mapping() -> None:
    config = load_config(
        {
            "LLM_WIKI_REPRESENTATION_MODEL": "test/qwen-vl",
            "LLM_WIKI_REPRESENTATION_DIMENSION": "1536",
            "LLM_WIKI_REPRESENTATION_DEVICE": "cpu",
            "LLM_WIKI_REPRESENTATION_TORCH_BACKEND": "cpu",
            "LLM_WIKI_REPRESENTATION_MAX_ITEMS": "7",
            "LLM_WIKI_REPRESENTATION_MAX_REQUEST_BYTES": "12345",
            "LLM_WIKI_REPRESENTATION_INSTRUCTION": "custom instruction",
        }
    )
    assert config.model == "test/qwen-vl"
    assert config.dimension == 1536
    assert config.max_items == 7
    assert config.max_request_bytes == 12345
    assert config.instruction == "custom instruction"
