from __future__ import annotations

import hashlib
import json
import sys
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from types import ModuleType

import pytest
from jose import jwt
from kogwistar.ontology import OntologyClassDescriptor, OntologyPackage

from kogwistar_llm_wiki import (
    IngestPipeline,
    WorkbenchApi,
    build_in_memory_namespace_engines,
    build_workbench_handler,
)
from kogwistar_llm_wiki.email import (
    EmailEvidenceRecord,
    EmailIngestRequest,
    EmailOntologyCatalog,
    InMemoryEmailEvidenceStore,
)

pytestmark = pytest.mark.usefixtures("install_fake_email_plugin")


def _install_fake_email_plugin(monkeypatch) -> ModuleType:
    """Exercise the host boundary without installing the private plugin repo."""
    package = OntologyPackage.create(
        ontology_id="email",
        version="1.0.0",
        title="Fake email ontology",
        descriptors=[
            OntologyClassDescriptor(
                descriptor_id="Attachment",
                name="Attachment",
                aliases=("attachment",),
                summary="A fake plugin descriptor for host contract tests.",
            )
        ],
    )
    plugin = ModuleType("kogwistar_email_plugin")
    plugin.render_email_viewer_plugin = lambda: (
        '<main><h1>Email plugin</h1><p id="status"></p>'
        '<script>document.getElementById("status").textContent = "ready"; '
        'fetch("/api/email/view"); fetch("/api/email/accept", '
        '{body: JSON.stringify({confirmed: true})}); '
        'fetch("/api/email/memory/promote"); '
        'const structural_proposals = {}; </script></main>'
    )

    class FakeEmailViewer:
        def __init__(self, store, *, authorize_stream, review_store=None) -> None:
            self.store = store
            self.authorize_stream = authorize_stream

        def get(self, *, workspace_id, stream_id, source_revision_id):
            if not self.authorize_stream(workspace_id, stream_id):
                raise PermissionError("email stream is not authorized")
            record = self.store.get(
                workspace_id=workspace_id,
                source_revision_id=source_revision_id,
            )
            if record is None or record.stream_id != stream_id:
                return {"status": "not_found"}
            parsed = record.parsed_payload
            plain_text = parsed.get("text_plain", [])
            body_text = "\n\n".join(str(item) for item in plain_text)
            return {
                "status": "ok",
                "mapping_id": record.mapping_payload.get("mapping_id"),
                "body_text": body_text,
                "structural_proposals": dict(record.mapping_payload),
            }

    plugin.EmailViewer = FakeEmailViewer
    plugin.email_ontology_json = lambda: package.model_dump(mode="json")
    monkeypatch.setitem(sys.modules, "kogwistar_email_plugin", plugin)
    return plugin


@pytest.fixture(autouse=True)
def _personal_mode_by_default(monkeypatch):
    """Keep HTTP contract tests independent of a developer's .env file."""
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")


def test_workbench_http_serves_lens_contract_without_core_changes(monkeypatch):
    # Do not let a developer's .env turn this personal-mode contract test into
    # an authenticated deployment.
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")
    engines = build_in_memory_namespace_engines()
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(WorkbenchApi(IngestPipeline(engines))))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/api/lens?workspace_id=http-test&query=missing")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["workspace_id"] == "http-test"
        assert payload["nodes"] == []
        proposal = json.dumps(
            {
                "request": {"workspace_id": "http-test", "query_text": "missing"},
                "proposal": {"lens_id": "wrong", "operation": "review", "evidence_ids": ["span:1"]},
            }
        ).encode()
        connection.request(
            "POST",
            "/api/proposal/validate",
            body=proposal,
            headers={"content-type": "application/json", "content-length": str(len(proposal))},
        )
        validation_response = connection.getresponse()
        validation = json.loads(validation_response.read())
        assert validation_response.status == 200
        assert validation["accepted"] is False
        assert validation["reason"] == "stale_lens_id"
        ask = json.dumps(
            {
                "workspace_id": "http-test",
                "query_text": "missing",
                "session_id": "browser-1",
                "mode": "deterministic",
            }
        ).encode()
        connection.request(
            "POST",
            "/api/ask",
            body=ask,
            headers={"content-type": "application/json", "content-length": str(len(ask))},
        )
        ask_response = connection.getresponse()
        ask_payload = json.loads(ask_response.read())
        assert ask_response.status == 200
        assert ask_payload["mode"] == "deterministic"
        assert ask_payload["history"]["session_id"] == "browser-1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        engines.close()


def test_workbench_http_exposes_container_health_endpoint():
    engines = build_in_memory_namespace_engines()
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(WorkbenchApi(IngestPipeline(engines))))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/healthz?workspace_id=container-test")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload == {"ok": True, "service": "kogwistar-llm-wiki", "workspace_id": "container-test"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        engines.close()


def test_workbench_http_serves_safe_email_viewer_plugin(monkeypatch) -> None:
    _install_fake_email_plugin(monkeypatch)
    engines = build_in_memory_namespace_engines()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        build_workbench_handler(WorkbenchApi(IngestPipeline(engines))),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/email/viewer")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert response.getheader("content-type") == "text/html; charset=utf-8"
        assert "content-security-policy" in {
            key.lower(): value for key, value in response.getheaders()
        }
        assert "textContent" in body
        assert "innerHTML" not in body
        assert "/api/email/view" in body
        assert "structural_proposals" in body
        assert "payload.proposals" not in body
        assert "/api/email/accept" in body
        assert "/api/email/memory/promote" in body
        assert "confirmed: true" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        engines.close()


def test_workbench_http_exposes_acl_checked_email_viewer(monkeypatch) -> None:
    _install_fake_email_plugin(monkeypatch)
    engines = build_in_memory_namespace_engines()
    store = InMemoryEmailEvidenceStore()
    content_sha256 = hashlib.sha256(b"raw email").hexdigest()
    store.put(
        EmailIngestRequest(
            workspace_id="email-http",
            stream_id="stream-1",
            source_key="uid:1",
            source_revision_id="revision-1",
            raw_bytes=b"raw email",
        ),
        EmailEvidenceRecord(
        workspace_id="email-http",
        stream_id="stream-1",
        source_key="uid:1",
        source_revision_id="revision-1",
        content_sha256=content_sha256,
        parsed_payload={
            "subject": "HTTP email",
            "date_header": None,
            "sender": [],
            "to": [],
            "cc": [],
            "text_plain": ["viewer body"],
            "attachments": [],
        },
        mapping_payload={"mapping_id": "mapping-1", "entities": [], "relations": []},
        blob_ref="sha256:" + content_sha256,
        ),
    )
    api = WorkbenchApi(
        IngestPipeline(engines),
        email_evidence_store=store,
        email_authorize_stream=lambda workspace, stream: (
            workspace == "email-http" and stream == "stream-1"
        ),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(api))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(
            "GET",
            "/api/email/view?workspace_id=email-http&stream_id=stream-1&source_revision_id=revision-1",
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["status"] == "ok"
        assert payload["mapping_id"] == "mapping-1"
        assert payload["body_text"] == "viewer body"
        assert payload["structural_proposals"] == {
            "mapping_id": "mapping-1",
            "entities": [],
            "relations": [],
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        api.close()
        engines.close()


def test_workbench_http_exposes_email_ontology_search(monkeypatch) -> None:
    plugin = _install_fake_email_plugin(monkeypatch)
    engines = build_in_memory_namespace_engines()
    api = WorkbenchApi(
        IngestPipeline(engines),
        email_ontology_catalog_factory=lambda workspace_id: EmailOntologyCatalog(
            workspace_id=workspace_id,
            packages=(OntologyPackage.model_validate(plugin.email_ontology_json()),),
        ),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(api))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(
            "GET",
            "/api/email/ontology/search?workspace_id=email-http&query=attachment",
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["status"] == "ok"
        assert any(item["descriptor_id"] == "Attachment" for item in payload["results"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        api.close()
        engines.close()


def test_workbench_http_enqueues_email_sync_without_credentials() -> None:
    class FakeScheduler:
        def __init__(self) -> None:
            self.request = None

        def enqueue(self, request):
            self.request = request
            return "email-job-1"

    engines = build_in_memory_namespace_engines()
    scheduler = FakeScheduler()
    api = WorkbenchApi(
        IngestPipeline(engines),
        email_sync_scheduler=scheduler,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(api))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps(
            {
                "workspace_id": "email-http",
                "connector_id": "connector-1",
                "cycle_id": "cycle-1",
                "owner_id": "worker-1",
            }
        ).encode()
        connection.request(
            "POST",
            "/api/email/sync/enqueue",
            body=body,
            headers={"content-type": "application/json", "content-length": str(len(body))},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 202
        assert payload == {
            "status": "queued",
            "workspace_id": "email-http",
            "connector_id": "connector-1",
            "cycle_id": "cycle-1",
            "job_id": "email-job-1",
        }
        assert scheduler.request is not None
        assert not hasattr(scheduler.request, "credential")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        api.close()
        engines.close()


def test_workbench_http_exposes_redacted_settings_and_staged_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("KOGWISTAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "never-return-this")
    engines = build_in_memory_namespace_engines()
    api = WorkbenchApi(IngestPipeline(engines), settings_path=str(tmp_path / "desired.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(api))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        auth_headers = {"Authorization": "Bearer never-return-this"}
        connection.request("GET", "/api/settings?workspace_id=settings-test", headers=auth_headers)
        response = connection.getresponse()
        snapshot = json.loads(response.read())
        assert response.status == 200
        assert snapshot["effective"]["workspace_id"] == "settings-test"
        assert "never-return-this" not in json.dumps(snapshot)
        body = json.dumps({"workspace_id": "settings-test", "settings": {"parser_model": "gpt-5-mini"}}).encode()
        connection.request("POST", "/api/settings/desired", body=body, headers={**auth_headers, "content-type": "application/json", "content-length": str(len(body))})
        updated = connection.getresponse()
        payload = json.loads(updated.read())
        assert updated.status == 200
        assert payload["desired"]["parser_model"] == "gpt-5-mini"
        assert payload["restart_required"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        api.close()
        engines.close()


def test_workbench_http_runs_codex_turn_as_durable_background_interaction(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")
    engines = build_in_memory_namespace_engines()
    api = WorkbenchApi(
        IngestPipeline(engines),
        agent_responder=lambda request, snapshot, progress: f"listener: {request.query_text}",
        codex_worker_count=1,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(api))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps(
            {"workspace_id": "http-background", "query_text": "Explain it", "session_id": "browser-2"}
        ).encode()
        connection.request(
            "POST",
            "/api/interactions",
            body=body,
            headers={"content-type": "application/json", "content-length": str(len(body))},
        )
        submitted = connection.getresponse()
        pending = json.loads(submitted.read())
        assert submitted.status == 202
        assert pending["status"] == "pending"

        deadline = time.monotonic() + 5
        result = pending
        while time.monotonic() < deadline and result["status"] == "pending":
            connection.request(
                "GET",
                "/api/interactions?workspace_id=http-background&interaction_id=" + pending["interaction_id"],
            )
            polled = connection.getresponse()
            assert polled.status == 200
            result = json.loads(polled.read())
            time.sleep(0.01)
        assert result["status"] == "completed"
        assert result["response"]["answer"]["text"] == "listener: Explain it"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        api.close()
        engines.close()


@pytest.mark.parametrize("mode", ["disabled", "static_token", "kogwistar_jwt"])
def test_workbench_http_auth_matrix_is_explicit_and_env_independent(monkeypatch, mode):
    """Exercise each supported mode instead of inheriting local .env state."""
    for name in (
        "LLM_WIKI_AUTH_MODE",
        "LLM_WIKI_AUTH_REQUIRED",
        "LLM_WIKI_API_TOKEN",
        "LLM_WIKI_MCP_TOKEN",
        "LLM_WIKI_API_TOKEN_SCOPES",
        "JWT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    headers = {}
    if mode == "static_token":
        monkeypatch.setenv("LLM_WIKI_AUTH_MODE", mode)
        monkeypatch.setenv("LLM_WIKI_API_TOKEN", "matrix-secret")
        headers = {"Authorization": "Bearer matrix-secret"}
    elif mode == "kogwistar_jwt":
        monkeypatch.setenv("LLM_WIKI_AUTH_MODE", mode)
        monkeypatch.setenv("JWT_SECRET", "matrix-secret")
        token = jwt.encode(
            {"sub": "matrix-user", "scope": "read", "workspaces": ["matrix"]},
            "matrix-secret",
            algorithm="HS256",
        )
        headers = {"Authorization": "Bearer " + token}
    else:
        monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")

    engines = build_in_memory_namespace_engines()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        build_workbench_handler(WorkbenchApi(IngestPipeline(engines))),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/api/lens?workspace_id=matrix&query=missing", headers=headers)
        response = connection.getresponse()
        response.read()
        assert response.status == 200

        if mode != "disabled":
            connection.request("GET", "/api/lens?workspace_id=matrix&query=missing")
            unauthorized = connection.getresponse()
            unauthorized.read()
            assert unauthorized.status == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        engines.close()
