from __future__ import annotations

import asyncio
import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from fastmcp.server.auth import AccessToken, AuthContext, run_auth_checks
from jose import jwt

from kogwistar_llm_wiki.agent_gateway import AgentGateway
from kogwistar_llm_wiki.mcp_agent_server import build_agent_mcp
from kogwistar_llm_wiki.workbench_http import (
    _payload_workspace,
    build_workbench_handler,
)


@pytest.fixture(autouse=True)
def _personal_mode_by_default(monkeypatch):
    """Do not let repository-local credentials alter protocol unit tests."""
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")


class FakeApi:
    dispatcher = None

    def __init__(self):
        self.interactions = SimpleNamespace(
            persist_proposal=lambda **_kwargs: SimpleNamespace(
                interaction_id="proposal-1",
                status="completed",
            )
        )

    def readiness(self):
        return {"ready": True, "service": "kogwistar-llm-wiki", "checks": {"fake": "open"}}

    def ask(self, payload):
        return {
            "mode": payload.get("mode", "deterministic"),
            "answer": {"text": f"grounded: {payload['query_text']}", "cited_entity_ids": ["ent:1"]},
            "snapshot": {"lens_id": "lens:1", "source_watermark": 7},
            "history": {"id": "hist:1"},
        }

    def get_lens(self, payload):
        return {"lens_id": "lens:1", "workspace_id": payload["workspace_id"], "nodes": [{"id": "ent:1"}]}

    def get_history(self, **_kwargs):
        return [{"id": "hist:1"}]

    def validate_proposal(self, payload):
        return {"accepted": True, "requires_confirmation": True, "reason": "no_change"}

    def confirm_cockpit_proposal(self, payload):
        return {"status": "rejected", "reason": "not configured in fake"}


def test_protocols_share_grounded_gateway_result():
    gateway = AgentGateway(FakeApi())
    responses = gateway.responses({"input": "What is here?", "metadata": {"workspace_id": "w"}})
    chat = gateway.chat_completions({"messages": [{"role": "user", "content": "What is here?"}], "workspace_id": "w"})

    assert responses["object"] == "response"
    assert responses["output"][0]["content"][0]["text"] == "grounded: What is here?"
    assert responses["llm_wiki"]["answer"]["cited_entity_ids"] == ["ent:1"]
    assert chat["choices"][0]["message"]["content"] == responses["output"][0]["content"][0]["text"]


def test_gateway_dispatches_query_history_and_controlled_mutation_tools():
    gateway = AgentGateway(FakeApi())
    query = gateway.call_mcp_tool("query", {"workspace_id": "w", "query_text": "hello"})
    history = gateway.call_mcp_tool("history", {"workspace_id": "w"})
    proposal = gateway.call_mcp_tool(
        "propose", {"request": {"workspace_id": "w", "query_text": "hello"}, "proposal": {}}
    )
    confirmation = gateway.call_mcp_tool(
        "confirm", {"workspace_id": "w", "interaction_id": "i", "confirmed": False}
    )
    assert query["answer"]["text"] == "grounded: hello"
    assert history["records"] == [{"id": "hist:1"}]
    assert proposal["requires_confirmation"] is True
    assert confirmation["status"] == "rejected"


def test_gateway_memory_capture_accepts_records_when_optional_record_is_null():
    captured: dict[str, object] = {}

    class MemoryApi(FakeApi):
        def capture_memory(self, payload):
            captured["payload"] = payload
            return {"status": "captured"}

    gateway = AgentGateway(MemoryApi())
    records = [{"workspace_id": "w", "statement": "fact"}]
    assert gateway.memory_capture({"record": None, "records": records}) == {"status": "captured"}
    assert captured["payload"] == records


def test_agent_protocol_routes_are_opt_in(monkeypatch):
    monkeypatch.delenv("LLM_WIKI_AGENT_API_ENABLED", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(FakeApi()))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/mcp/tools/list")
        response = connection.getresponse()
        assert response.status == 404
        assert json.loads(response.read())["error"] == "agent_api_disabled"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_discovery_and_readiness_endpoints(monkeypatch):
    monkeypatch.delenv("LLM_WIKI_AGENT_API_ENABLED", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(FakeApi()))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        for path in ("/healthz", "/readyz", "/api/capabilities", "/v1/models"):
            connection.request("GET", path)
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload
        connection.request("GET", "/api/capabilities")
        capabilities = json.loads(connection.getresponse().read())
        assert capabilities["api_version"] == "v1"
        assert capabilities["protocols"]["mcp"] is True
        assert capabilities["mcp_tools"] == [
            "query", "search", "ingest", "source", "reingest", "maintain",
            "status", "hypergraph_search", "history", "memory_recall",
            "memory_capture", "memory_review", "propose", "confirm",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_agent_routes_require_bearer_token_and_scope(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AGENT_API_ENABLED", "true")
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_AUTH_REQUIRED", "true")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "secret")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN_SCOPES", "read")
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(FakeApi()))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/mcp/tools/list")
        unauthorized = connection.getresponse()
        assert unauthorized.status == 401
        unauthorized.read()

        connection.request("GET", "/mcp/tools/list", headers={"Authorization": "Bearer secret"})
        authorized = connection.getresponse()
        assert authorized.status == 200
        authorized.read()

        body = json.dumps({"name": "search", "arguments": {"workspace_id": "w", "query_text": "q"}}).encode()
        connection.request(
            "POST",
            "/mcp/tools/call",
            body=body,
            headers={"Authorization": "Bearer secret", "content-type": "application/json", "content-length": str(len(body))},
        )
        readable = connection.getresponse()
        assert readable.status == 200
        readable.read()

        body = json.dumps({"name": "confirm", "arguments": {"workspace_id": "w", "interaction_id": "i", "confirmed": False}}).encode()
        connection.request(
            "POST",
            "/mcp/tools/call",
            body=body,
            headers={"Authorization": "Bearer secret", "content-type": "application/json", "content-length": str(len(body))},
        )
        forbidden = connection.getresponse()
        assert forbidden.status == 403
        forbidden.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_jwt_auth_binds_http_request_to_workspace_and_core_claims(monkeypatch):
    class ClaimsApi(FakeApi):
        def get_lens(self, payload):
            from kogwistar.server.auth_middleware import claims_ctx

            claims = claims_ctx.get() or {}
            result = super().get_lens(payload)
            result["principal"] = claims.get("sub")
            return result

    monkeypatch.setenv("LLM_WIKI_AGENT_API_ENABLED", "true")
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "kogwistar_jwt")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    token = jwt.encode(
        {"sub": "alice", "scope": "read", "workspaces": ["team-a"]},
        "test-secret",
        algorithm="HS256",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(ClaimsApi()))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Authorization": "Bearer " + token}
        connection.request("GET", "/api/lens?workspace_id=team-a&query=q", headers=headers)
        allowed = connection.getresponse()
        allowed_payload = json.loads(allowed.read())
        assert allowed.status == 200
        assert allowed_payload["principal"] == "alice"

        connection.request("GET", "/api/lens?workspace_id=team-b&query=q", headers=headers)
        denied = connection.getresponse()
        assert denied.status == 403
        assert "not a member" in json.loads(denied.read())["detail"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_native_mcp_requires_configured_token_when_requested(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_MCP_AUTH_REQUIRED", "true")
    monkeypatch.delenv("LLM_WIKI_MCP_TOKEN", raising=False)
    monkeypatch.delenv("LLM_WIKI_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="no token is configured"):
        build_agent_mcp(AgentGateway(FakeApi()))


def test_native_mcp_accepts_explicit_token(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_MCP_AUTH_REQUIRED", "true")
    monkeypatch.setenv("LLM_WIKI_MCP_TOKEN", "secret")
    mcp = build_agent_mcp(AgentGateway(FakeApi()))
    assert mcp.auth is not None


def test_native_mcp_explicit_no_auth_does_not_enable_token_verifier(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_MCP_AUTH_REQUIRED", "false")
    monkeypatch.setenv("LLM_WIKI_MCP_TOKEN", "present-but-disabled")
    mcp = build_agent_mcp(AgentGateway(FakeApi()))
    assert mcp.auth is None


def test_native_mcp_jwt_does_not_require_a_static_token(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "kogwistar_jwt")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("LLM_WIKI_MCP_AUTH_REQUIRED", "true")
    monkeypatch.delenv("LLM_WIKI_MCP_TOKEN", raising=False)
    monkeypatch.delenv("LLM_WIKI_API_TOKEN", raising=False)
    mcp = build_agent_mcp(AgentGateway(FakeApi()))
    assert mcp.auth is not None


def test_protocol_workspace_resolution_rejects_conflicting_envelopes():
    assert _payload_workspace(
        {"params": {"workspace_id": "team-a"}, "arguments": {"workspace_id": "team-a"}}
    ) == "team-a"
    with pytest.raises(ValueError, match="conflicting workspace_id"):
        _payload_workspace(
            {"workspace_id": "team-a", "arguments": {"workspace_id": "team-b"}}
        )


def test_rest_handler_rejects_invalid_auth_mode_at_construction(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "typo_jwt")
    with pytest.raises(ValueError, match="must be disabled"):
        build_workbench_handler(FakeApi())


def test_native_mcp_shared_auth_is_used_when_mcp_overrides_are_empty(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_AUTH_REQUIRED", "true")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "shared-secret")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN_SCOPES", "read")
    monkeypatch.setenv("LLM_WIKI_MCP_AUTH_REQUIRED", "")
    monkeypatch.setenv("LLM_WIKI_MCP_TOKEN", "")
    monkeypatch.setenv("LLM_WIKI_MCP_TOKEN_SCOPES", "")

    mcp = build_agent_mcp(AgentGateway(FakeApi()))

    assert mcp.auth is not None


def test_native_mcp_registers_exact_semantic_tools_and_descriptions():
    mcp = build_agent_mcp(AgentGateway(FakeApi()))
    tools = asyncio.run(mcp.list_tools())
    assert [tool.name for tool in tools] == [
        "query", "search", "ingest", "source", "reingest", "maintain",
        "status", "hypergraph_search", "history", "memory_recall",
        "memory_capture", "memory_review", "propose", "confirm",
    ]
    assert all(tool.description for tool in tools)
    query = next(tool for tool in tools if tool.name == "query")
    assert query.parameters["required"] == ["workspace_id", "query_text"]
    reingest = next(tool for tool in tools if tool.name == "reingest")
    assert "source_document_id" in reingest.parameters["properties"]


def test_native_mcp_applies_read_and_write_scopes_to_tools(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_MCP_AUTH_REQUIRED", "true")
    monkeypatch.setenv("LLM_WIKI_MCP_TOKEN", "secret")
    mcp = build_agent_mcp(AgentGateway(FakeApi()))
    # The provider-level list is intentionally unfiltered; list_tools() needs a
    # live transport auth context and would hide every tool in this unit test.
    tools = {tool.name: tool for tool in asyncio.run(mcp._list_tools())}
    read_token = AccessToken(token="secret", client_id="client", scopes=["read"])
    write_token = AccessToken(token="secret", client_id="client", scopes=["write"])
    def read_ctx(name, token):
        return AuthContext(token=token, component=tools[name])

    assert asyncio.run(run_auth_checks(tools["search"].auth, read_ctx("search", read_token))) is True
    assert asyncio.run(run_auth_checks(tools["confirm"].auth, read_ctx("confirm", read_token))) is False
    assert asyncio.run(run_auth_checks(tools["confirm"].auth, read_ctx("confirm", write_token))) is True
    assert asyncio.run(run_auth_checks(tools["query"].auth, read_ctx("query", write_token))) is False


def test_agent_protocol_routes_expose_response_chat_a2a_and_mcp(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AGENT_API_ENABLED", "true")
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(FakeApi()))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        for path, body, expected in [
            ("/v1/responses", {"input": "hello", "workspace_id": "w"}, "response"),
            ("/v1/chat/completions", {"messages": [{"role": "user", "content": "hello"}], "workspace_id": "w"}, "chat.completion"),
            ("/a2a/v1/message:send", {"message": {"parts": [{"text": "hello"}]}, "workspace_id": "w", "background": False}, None),
        ]:
            encoded = json.dumps(body).encode()
            connection.request("POST", path, body=encoded, headers={"content-type": "application/json", "content-length": str(len(encoded))})
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            if expected:
                assert payload["object"] == expected
            else:
                assert payload["status"]["state"] == "completed"

        connection.request("GET", "/mcp/tools/list")
        response = connection.getresponse()
        assert response.status == 200
        assert {tool["name"] for tool in json.loads(response.read())["tools"]} == {
            "query", "search", "ingest", "source", "reingest", "maintain",
            "status", "hypergraph_search", "history", "memory_recall",
            "memory_capture", "memory_review", "propose", "confirm",
        }

        encoded = json.dumps({"message": {"parts": [{"text": "hello"}]}, "workspace_id": "w", "background": False}).encode()
        connection.request("POST", "/a2a/v1/message:stream", body=encoded, headers={"content-type": "application/json", "content-length": str(len(encoded))})
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("content-type", "").startswith("text/event-stream")
        assert "event: task" in response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_openai_content_parts_are_normalized_to_text():
    class RecordingApi(FakeApi):
        def ask(self, payload):
            self.last_payload = payload
            return super().ask(payload)

    api = RecordingApi()
    gateway = AgentGateway(api)
    gateway.chat_completions(
        {
            "workspace_id": "content-parts",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "first"},
                        {"type": "text", "text": "second"},
                    ],
                }
            ],
        }
    )
    assert api.last_payload["query_text"] == "first\nsecond"


def test_a2a_stream_polls_background_task_until_terminal(monkeypatch):
    class BackgroundApi(FakeApi):
        dispatcher = object()

        def __init__(self):
            self.polls = 0

        def submit_interaction(self, payload):
            return {
                "interaction_id": "task-stream-1",
                "workspace_id": payload["workspace_id"],
                "status": "pending",
            }

        def get_interaction(self, *, workspace_id, interaction_id):
            self.polls += 1
            return {
                "interaction_id": interaction_id,
                "workspace_id": workspace_id,
                "status": "completed" if self.polls >= 1 else "pending",
                "response": {"answer": {"text": "finished"}},
            }

    monkeypatch.setenv("LLM_WIKI_AGENT_API_ENABLED", "true")
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")
    monkeypatch.setenv("LLM_WIKI_A2A_STREAM_POLL_SECONDS", "0.1")
    api = BackgroundApi()
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(api))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps(
            {"message": {"parts": [{"text": "hello"}]}, "workspace_id": "stream", "background": True}
        ).encode()
        connection.request(
            "POST",
            "/a2a/v1/message:stream",
            body=body,
            headers={"content-type": "application/json", "content-length": str(len(body))},
        )
        response = connection.getresponse()
        stream = response.read().decode()
        assert response.status == 202
        assert stream.count("event: task") == 2
        assert '"state": "completed"' in stream
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a2a_jsonrpc_binding_and_agent_card_contract(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AGENT_API_ENABLED", "true")
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "static_token")
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_workbench_handler(FakeApi()))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {
            "authorization": "Bearer secret",
            "content-type": "application/json",
        }
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "message/send",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "hello"}]},
                    "metadata": {"workspace_id": "w"},
                    "configuration": {"blocking": True},
                },
            }
        ).encode()
        headers["content-length"] = str(len(body))
        connection.request("POST", "/a2a", body=body, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 7
        assert payload["result"]["status"]["state"] == "completed"

        connection.request("GET", "/.well-known/agent.json", headers={"authorization": "Bearer secret"})
        response = connection.getresponse()
        card = json.loads(response.read())
        assert card["protocolVersion"] == "0.2.6"
        assert card["preferredTransport"] == "JSONRPC"
        assert card["url"].endswith("/a2a")
        assert card["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
        assert {skill["id"] for skill in card["skills"]} >= {"query", "ingest", "confirm"}

        body = json.dumps({"jsonrpc": "2.0", "id": 8, "method": "not/a/method", "params": {}}).encode()
        headers["content-length"] = str(len(body))
        connection.request("POST", "/a2a", body=body, headers=headers)
        response = connection.getresponse()
        error = json.loads(response.read())
        assert response.status == 400
        assert error["error"]["code"] == -32601
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
