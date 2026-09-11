from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from llm_wiki_embedding_contract import EmbeddingProfile
from llm_wiki_embedding_service.app import create_app
from llm_wiki_embedding_service.config import EmbeddingServiceConfig, load_config


class FakeEncoder:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile

    def encode(self, items):
        return [((1.0,) + (0.0,) * (self.profile.dimension - 1),) for _ in items]


def _config() -> EmbeddingServiceConfig:
    return EmbeddingServiceConfig(dimension=64, revision="test-revision", token="secret")


def test_standalone_import_does_not_load_application_package() -> None:
    source_root = Path(__file__).parents[2] / "src"
    service_files = list((source_root / "llm_wiki_embedding_service").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in service_files)
    assert "kogwistar_llm_wiki" not in text
    assert "kogwistar" not in text


def test_service_contract_preserves_auth_profile_and_order() -> None:
    config = _config()
    with TestClient(create_app(encoder=FakeEncoder(config.profile), config=config)) as client:
        assert client.get("/healthz").json()["ok"] is True
        assert client.get("/readyz").status_code == 200
        assert client.get("/v1/capabilities").status_code == 401
        response = client.post(
            "/v1/represent",
            headers={"Authorization": "Bearer secret"},
            json={
                "contract_version": "v1",
                "request_id": "test",
                "operation": "query",
                "profile_fingerprint": config.profile.fingerprint,
                "items": [
                    {"item_id": "second", "modality": "text", "text": "b"},
                    {"item_id": "first", "modality": "text", "text": "a"},
                ],
            },
        )
        assert response.status_code == 200
        assert [item["item_id"] for item in response.json()["results"]] == ["second", "first"]


def test_service_rejects_path_and_bad_asset_hash() -> None:
    config = _config()
    with TestClient(create_app(encoder=FakeEncoder(config.profile), config=config)) as client:
        response = client.post(
            "/v1/represent",
            headers={"Authorization": "Bearer secret"},
            json={
                "contract_version": "v1",
                "operation": "document",
                "profile_fingerprint": config.profile.fingerprint,
                "items": [{"item_id": "x", "modality": "image", "asset": {"path": "C:/secret.png"}}],
            },
        )
        assert response.status_code == 422


def test_revision_is_required_and_runtime_backend_is_explicit() -> None:
    with pytest.raises(ValueError, match="revision"):
        load_config({})
    config = load_config({
        "LLM_WIKI_EMBEDDING_MODEL_REVISION": "abc123",
        "LLM_WIKI_EMBEDDING_TORCH_BACKEND": "cu128",
        "LLM_WIKI_EMBEDDING_DEVICE": "cuda",
    })
    assert config.revision == "abc123"
    assert config.torch_backend == "cu128"
    assert config.profile.dimension == 1024


def test_standalone_service_has_no_sibling_runtime_dependencies() -> None:
    source_root = Path(__file__).parents[2] / "src"
    files = list((source_root / "llm_wiki_embedding_service").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert all(name not in text for name in ("kg_doc_parser", "kogwistar_obsidian_sink", "chromadb", "psycopg", "mcp"))
