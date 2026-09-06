from __future__ import annotations

import json
from http.client import HTTPConnection
from threading import Thread
import time

from kogwistar_llm_wiki import (
    IngestPipeline,
    WorkbenchApi,
    build_in_memory_namespace_engines,
    build_workbench_handler,
)
from http.server import ThreadingHTTPServer


def test_workbench_http_serves_lens_contract_without_core_changes():
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


def test_workbench_http_runs_codex_turn_as_durable_background_interaction():
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
