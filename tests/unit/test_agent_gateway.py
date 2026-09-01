from __future__ import annotations

import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

from kogwistar_llm_wiki.agent_gateway import AgentGateway
from kogwistar_llm_wiki.workbench_http import build_workbench_handler


class FakeApi:
    dispatcher = None

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


def test_agent_protocol_routes_expose_response_chat_a2a_and_mcp(monkeypatch):
    monkeypatch.setenv("LLM_WIKI_AGENT_API_ENABLED", "true")
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
        assert {tool["name"] for tool in json.loads(response.read())["tools"]} >= {"llm_wiki.ask", "llm_wiki.confirm"}

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
    response = gateway.chat_completions(
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
