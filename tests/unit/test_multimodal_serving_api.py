from __future__ import annotations

import json
from hashlib import sha256
from http.client import HTTPConnection
from threading import Thread

import pytest

from kogwistar_llm_wiki import IngestPipeline, build_in_memory_namespace_engines
from kogwistar_llm_wiki.agent.gateway import AgentGateway
from kogwistar_llm_wiki.embeddings.multimodal_projection import (
    FakeMultimodalEncoder,
    InMemoryMultimodalProjectionStore,
    MultimodalEmbeddingProfile,
)
from kogwistar_llm_wiki.embeddings.multimodal_sources import MappingAssetResolver
from kogwistar_llm_wiki.models import IngestPipelineRequest
from kogwistar_llm_wiki.workbench.workbench_api import WorkbenchApi
from kogwistar_llm_wiki.workbench.workbench_http import build_workbench_handler


def _pipeline():
    engines = build_in_memory_namespace_engines()
    profile = MultimodalEmbeddingProfile(
        provider="fake",
        model="serving-contract-fixture",
        embedding="late_interaction",
        dimension=8,
    )
    encoder = FakeMultimodalEncoder(profile=profile)
    store = InMemoryMultimodalProjectionStore(scope="demo:multimodal", profile=profile)
    return engines, IngestPipeline(
        engines,
        multimodal_projection_store=store,
        multimodal_encoder=encoder,
    )


def _unit(view_id: str = "image-region-1", *, workspace_id: str = "demo") -> dict[str, object]:
    return {
        "view_id": view_id,
        "workspace_id": workspace_id,
        "source_id": "source-1",
        "source_revision_id": "source-1@rev-1",
        "source_namespace": workspace_id,
        "modality": "text",
        "locator": {
            "kind": "text_range",
            "start_char": 0,
            "end_char": len("a rabbit in a lecture"),
        },
        "text": "a rabbit in a lecture",
    }


def _api(pipeline: IngestPipeline) -> WorkbenchApi:
    return WorkbenchApi(
        pipeline,
        resource_authorizer=lambda _workspace, _kind, _resource, _action: True,
        multimodal_source_map_resolver=lambda unit: {
            "workspace_id": unit.workspace_id,
            "source_namespace": unit.source_namespace or unit.workspace_id,
            "source_id": unit.source_id,
            "source_revision_id": unit.source_revision_id,
            "raw_text": unit.text,
            "source_digest": sha256((unit.text or "").encode("utf-8")).hexdigest(),
            "content_ref": unit.content_ref,
            "asset_sha256": unit.asset_sha256,
        },
    )


@pytest.mark.ci
def test_multimodal_public_lifecycle_persists_and_searches() -> None:
    engines, pipeline = _pipeline()
    try:
        api = _api(pipeline)
        captured = api.multimodal_capture(
            {"workspace_id": "demo", "units": [_unit()]}
        )
        assert captured["status"] == "captured"
        assert captured["stage_counts"] == {
            "stage1": 1,
            "stage2": 0,
            "pending_stage2": 1,
        }

        indexed = api.multimodal_index({"workspace_id": "demo", "batch_size": 1})
        assert indexed["status"] == "indexed"
        assert indexed["embedded_count"] == 1
        assert indexed["stage_counts"]["pending_stage2"] == 0

        status = api.multimodal_status(workspace_id="demo")
        assert status["status"] == "ready"
        assert status["stage_counts"]["stage2"] == 1

        result = api.multimodal_search(
            {"workspace_id": "demo", "query_text": "rabbit lecture", "limit": 1}
        )
        assert result["status"] == "ok"
        assert result["hits"][0]["source_revision_id"] == "source-1@rev-1"
        assert result["hits"][0]["dereference_status"] == "unresolved"
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_mcp_tools_expose_the_same_host_validated_lifecycle() -> None:
    engines, pipeline = _pipeline()
    try:
        gateway = AgentGateway(_api(pipeline))
        names = gateway.mcp_tool_names()
        assert {
            "multimodal_capture",
            "multimodal_index",
            "multimodal_search",
            "multimodal_status",
        } <= set(names)
        gateway.call_mcp_tool(
            "multimodal_capture", {"workspace_id": "demo", "units": [_unit()]}
        )
        indexed = gateway.call_mcp_tool(
            "multimodal_index", {"workspace_id": "demo", "batch_size": 1}
        )
        assert indexed["embedded_count"] == 1
        searched = gateway.call_mcp_tool(
            "multimodal_search",
            {"workspace_id": "demo", "query_text": "rabbit", "limit": 1},
        )
        assert searched["status"] == "ok"
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_capture_rejects_cross_workspace_and_caller_references() -> None:
    engines, pipeline = _pipeline()
    try:
        api = _api(pipeline)
        with pytest.raises(PermissionError, match="different workspace"):
            api.multimodal_capture(
                {"workspace_id": "demo", "units": [_unit(workspace_id="other")]}
            )
        supplied_reference = _unit()
        supplied_reference["embedding_reference"] = {}
        with pytest.raises(ValueError, match="embedding_reference"):
            api.multimodal_capture(
                {"workspace_id": "demo", "units": [supplied_reference]}
            )
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_public_search_fails_closed_without_source_acl() -> None:
    engines, pipeline = _pipeline()
    try:
        api = WorkbenchApi(pipeline)
        with pytest.raises(PermissionError, match="not authorized"):
            api.multimodal_capture({"workspace_id": "demo", "units": [_unit()]})
        with pytest.raises(PermissionError, match="not authorized"):
            api.multimodal_index({"workspace_id": "demo"})
        with pytest.raises(PermissionError, match="not authorized"):
            api.multimodal_status(workspace_id="demo")
        result = api.multimodal_search(
            {"workspace_id": "demo", "query_text": "rabbit"}
        )
        assert result["status"] == "degraded"
        assert result["reason"] == "source_authorization_not_configured"
        assert result["hits"] == []
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_index_is_workspace_scoped_in_a_shared_projection_store() -> None:
    engines, pipeline = _pipeline()
    try:
        api = _api(pipeline)
        first = _unit("demo-view", workspace_id="demo")
        second = _unit("other-view", workspace_id="other")
        api.multimodal_capture({"workspace_id": "demo", "units": [first]})
        api.multimodal_capture({"workspace_id": "other", "units": [second]})
        result = api.multimodal_index({"workspace_id": "demo", "batch_size": 2})
        assert result["embedded_count"] == 1
        assert result["stage_counts"] == {
            "stage1": 1,
            "stage2": 1,
            "pending_stage2": 0,
        }
        other_status = api.multimodal_status(workspace_id="other")
        assert other_status["stage_counts"] == {
            "stage1": 1,
            "stage2": 0,
            "pending_stage2": 1,
        }
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_index_batch_size_limits_pending_units() -> None:
    engines, pipeline = _pipeline()
    try:
        api = _api(pipeline)
        api.multimodal_capture(
            {
                "workspace_id": "demo",
                "units": [_unit(f"view-{index}") for index in range(3)],
            }
        )
        result = api.multimodal_index({"workspace_id": "demo", "batch_size": 1})
        assert result["embedded_count"] == 1
        assert result["stage_counts"] == {
            "stage1": 3,
            "stage2": 1,
            "pending_stage2": 2,
        }
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_capture_rejects_authoritative_source_span_mismatch() -> None:
    engines, pipeline = _pipeline()
    try:
        api = WorkbenchApi(
            pipeline,
            resource_authorizer=lambda _workspace, _kind, _resource, _action: True,
            multimodal_source_map_resolver=lambda unit: {
                "workspace_id": unit.workspace_id,
                "source_namespace": unit.source_namespace or unit.workspace_id,
                "source_id": unit.source_id,
                "source_revision_id": unit.source_revision_id,
                "raw_text": "authoritative source text",
            },
        )
        with pytest.raises(ValueError, match="does not match the source map span"):
            api.multimodal_capture({"workspace_id": "demo", "units": [_unit()]})
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_capture_resolves_registered_revision_document() -> None:
    engines, pipeline = _pipeline()
    try:
        source_text = "a rabbit in a lecture"
        request = IngestPipelineRequest(
            workspace_id="demo",
            source_uri="memory://lecture",
            title="Lecture",
            raw_text=source_text,
        )
        pipeline.register_source(
            request=request,
            source_document_id="source-1",
            namespace="conv:bg:demo",
        )
        revision = pipeline.source_revision(
            request=request,
            source_document_id="source-1",
        )
        unit = _unit("registered-view")
        unit["source_revision_id"] = revision.revision_id
        unit["source_namespace"] = pipeline.namespaces_for("demo").source_space
        api = WorkbenchApi(
            pipeline,
            resource_authorizer=lambda _workspace, _kind, _resource, _action: True,
        )
        captured = api.multimodal_capture({"workspace_id": "demo", "units": [unit]})
        assert captured["status"] == "captured"
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_capture_preflights_duplicate_batch_without_partial_write() -> None:
    engines, pipeline = _pipeline()
    try:
        api = _api(pipeline)
        with pytest.raises(ValueError, match="duplicate multimodal source view"):
            api.multimodal_capture(
                {
                    "workspace_id": "demo",
                    "units": [_unit("same-view"), _unit("same-view")],
                }
            )
        assert pipeline.multimodal_projection_store is not None
        assert pipeline.multimodal_projection_store.get(
            "same-view", profile=pipeline.multimodal_encoder.profile, workspace_id="demo"
        ) is None
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_search_resolves_an_authorized_image_query_reference() -> None:
    engines, pipeline = _pipeline()
    try:
        api = WorkbenchApi(
            pipeline,
            resource_authorizer=lambda _workspace, _kind, _resource, _action: True,
            multimodal_source_map_resolver=lambda unit: {
                "workspace_id": unit.workspace_id,
                    "source_namespace": unit.source_namespace or unit.workspace_id,
                "source_id": unit.source_id,
                "source_revision_id": unit.source_revision_id,
                "raw_text": unit.text,
                "source_digest": sha256((unit.text or "").encode("utf-8")).hexdigest(),
                "content_ref": unit.content_ref,
                "asset_sha256": unit.asset_sha256,
            },
            multimodal_asset_resolver=MappingAssetResolver(
                {"object://query-image": b"not-a-real-image-for-the-fake-encoder"}
            ),
        )
        api.multimodal_capture({"workspace_id": "demo", "units": [_unit()]})
        api.multimodal_index({"workspace_id": "demo"})
        result = api.multimodal_search(
            {
                "workspace_id": "demo",
                "image_content_ref": "object://query-image",
                "limit": 1,
            }
        )
        assert result["status"] == "ok"
        assert len(result["hits"]) == 1
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_search_rejects_an_unauthorized_image_query_reference() -> None:
    engines, pipeline = _pipeline()
    try:
        api = WorkbenchApi(
            pipeline,
            resource_authorizer=lambda _workspace, kind, _resource, _action: kind != "asset",
            multimodal_asset_resolver=MappingAssetResolver(
                {"object://query-image": b"not-a-real-image-for-the-fake-encoder"}
            ),
        )
        with pytest.raises(PermissionError, match="query asset"):
            api.multimodal_search(
                {
                    "workspace_id": "demo",
                    "image_content_ref": "object://query-image",
                }
            )
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_search_enforces_namespace_and_revision_acl() -> None:
    engines, pipeline = _pipeline()
    try:
        api = _api(pipeline)
        api.multimodal_capture({"workspace_id": "demo", "units": [_unit()]})
        api.multimodal_index({"workspace_id": "demo"})

        denied_kinds = {"source_namespace", "source_revision"}
        restricted = WorkbenchApi(
            pipeline,
            resource_authorizer=lambda _workspace, kind, _resource, _action: kind
            not in denied_kinds,
        )
        result = restricted.multimodal_search(
            {"workspace_id": "demo", "query_text": "rabbit", "limit": 1}
        )
        assert result["status"] == "ok"
        assert result["hits"] == []
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_capture_requires_write_access_to_a_foreign_source_namespace() -> None:
    engines, pipeline = _pipeline()
    try:
        api = WorkbenchApi(
            pipeline,
            resource_authorizer=lambda _workspace, kind, resource, action: not (
                kind == "source_namespace"
                and resource == "foreign"
                and action == "write"
            ),
            multimodal_source_map_resolver=lambda unit: {
                "workspace_id": unit.workspace_id,
                "source_namespace": unit.source_namespace or unit.workspace_id,
                "source_id": unit.source_id,
                "source_revision_id": unit.source_revision_id,
                "raw_text": unit.text,
                "source_digest": sha256((unit.text or "").encode("utf-8")).hexdigest(),
            },
        )
        foreign = _unit()
        foreign["source_namespace"] = "foreign"
        with pytest.raises(PermissionError, match="source namespace"):
            api.multimodal_capture({"workspace_id": "demo", "units": [foreign]})
    finally:
        engines.close()


@pytest.mark.ci
def test_multimodal_rest_endpoints_cover_capture_index_search_and_status(monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")
    engines, pipeline = _pipeline()
    server = None
    thread = None
    try:
        api = _api(pipeline)
        from http.server import ThreadingHTTPServer

        server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(api))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)

        def post(path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
            encoded = json.dumps(payload).encode("utf-8")
            connection.request(
                "POST",
                path,
                body=encoded,
                headers={
                    "content-type": "application/json",
                    "content-length": str(len(encoded)),
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        status, captured = post(
            "/api/multimodal/capture",
            {"workspace_id": "demo", "units": [_unit()]},
        )
        assert status == 201 and captured["count"] == 1
        status, indexed = post("/api/multimodal/index", {"workspace_id": "demo"})
        assert status == 200 and indexed["embedded_count"] == 1
        status, searched = post(
            "/api/multimodal/search",
            {"workspace_id": "demo", "query_text": "rabbit"},
        )
        assert status == 200 and searched["status"] == "ok"

        connection.request("GET", "/api/multimodal/status?workspace_id=demo")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["stage_counts"]["stage2"] == 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        engines.close()
