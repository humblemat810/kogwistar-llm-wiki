from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from kogwistar_llm_wiki.codex_workbench_agent import (
    CodexAppServerRunner,
    CodexCliCockpitResponder,
    CodexCliResponder,
    CodexCliSettings,
    CodexProcessRunner,
    HostCockpitResponder,
    _cockpit_transport_schema,
    _strict_output_schema,
)
from kogwistar_llm_wiki.semantic_lens import SemanticLensRequest, SemanticLensSnapshot
from kogwistar_llm_wiki.workbench_cockpit import CockpitAction


class FakeRunner:
    def __init__(self) -> None:
        self.prompt = ""
        self.progress_count = 0

    def run(self, *, settings, prompt, progress, trace_line=None):
        assert settings.model == "test-model"
        self.prompt = prompt
        progress()
        self.progress_count += 1
        return "Grounded response [node-1]; no_change"


def test_codex_responder_sends_only_bounded_lens_and_reports_progress():
    runner = FakeRunner()
    progress: list[str] = []
    responder = CodexCliResponder(CodexCliSettings(model="test-model"), runner=runner)
    request = SemanticLensRequest(workspace_id="agent-test", query_text="What follows?")
    snapshot = SemanticLensSnapshot(
        lens_id="lens-1",
        workspace_id="agent-test",
        source_watermark=7,
        projected_at_ms=1,
        completeness="bounded",
        nodes=(),
        edges=(),
        hyperedges=(),
        participations=(),
        anchor_explanations=(),
        selection_explanations=(),
        omitted_summary={},
        query_timing_ms=1,
    )

    answer = responder(request, snapshot, lambda: progress.append("tick"))

    assert answer == "Grounded response [node-1]; no_change"
    assert "What follows?" in runner.prompt
    assert '"lens_id": "lens-1"' in runner.prompt
    assert "Graph edits are optional" in runner.prompt
    assert len(progress) == 2


class CockpitRunner(FakeRunner):
    def run(self, *, settings, prompt, progress, trace_line=None):
        self.prompt = prompt
        progress()
        return '{"kind":"no_change","rationale":"No grounded change is warranted."}'


def test_codex_cockpit_responder_requires_a_typed_json_action():
    runner = CockpitRunner()
    responder = CodexCliCockpitResponder(CodexCliSettings(model="test-model"), runner=runner)
    request = SemanticLensRequest(workspace_id="agent-test", query_text="What follows?")
    snapshot = SemanticLensSnapshot(
        lens_id="lens-1",
        workspace_id="agent-test",
        source_watermark=7,
        projected_at_ms=1,
        completeness="bounded",
        nodes=(),
        edges=(),
        hyperedges=(),
        participations=(),
        anchor_explanations=(),
        selection_explanations=(),
        omitted_summary={},
        query_timing_ms=1,
    )

    action = responder(request, snapshot, (), lambda: None)

    assert action.kind == "no_change"
    assert "action_schema" in runner.prompt
    assert "Return exactly one JSON object" in runner.prompt


def test_codex_cockpit_retries_once_with_the_validation_error():
    class RepairRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.prompts: list[str] = []

        def run(self, *, settings, prompt, progress, trace_line=None):
            self.prompts.append(prompt)
            progress()
            return "not valid json" if len(self.prompts) == 1 else '{"kind":"no_change"}'

    runner = RepairRunner()
    responder = CodexCliCockpitResponder(CodexCliSettings(model="test-model"), runner=runner)
    action = responder(
        SemanticLensRequest(workspace_id="agent-test", query_text="What follows?"),
        _snapshot(),
        (),
    )
    assert action.kind == "no_change"
    assert len(runner.prompts) == 2
    assert "previous action was invalid" in runner.prompts[1]


def test_process_runner_passes_the_typed_output_schema(monkeypatch):
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            captured["command"] = command
            output_path = Path(command[command.index("-o") + 1])
            schema_path = Path(command[command.index("--output-schema") + 1])
            captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
            output_path.write_text('{"kind":"no_change"}', encoding="utf-8")
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

        def kill(self):
            return None

    monkeypatch.setattr("kogwistar_llm_wiki.codex_workbench_agent.subprocess.Popen", FakeProcess)
    runner = CodexProcessRunner()
    raw = runner.run(
        settings=CodexCliSettings(executable="codex"),
        prompt="return JSON",
        progress=lambda: None,
        output_schema={"type": "object", "properties": {"kind": {"type": "string"}}},
    )
    command = captured["command"]
    assert raw == '{"kind":"no_change"}'
    assert "--output-schema" in command
    assert captured["schema"]["type"] == "object"
    assert captured["schema"]["required"] == ["kind"]
    assert captured["schema"]["additionalProperties"] is False


def test_app_server_runner_handshakes_streams_and_closes(monkeypatch):
    captured: dict[str, object] = {"messages": []}

    messages = [
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "result": {"thread": {"id": "thread-1"}}},
        {"jsonrpc": "2.0", "id": 3, "result": {"turn": {"id": "turn-1", "status": "inProgress"}}},
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "Hello "},
        },
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "world"},
        },
        {
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed", "items": []},
            },
        },
    ]

    class FakeStdin(io.StringIO):
        def write(self, value):
            captured["messages"].append(json.loads(value))
            return super().write(value)

        def flush(self):
            return None

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            captured["command"] = command
            self.stdin = FakeStdin()
            self.stdout = io.StringIO("\n".join(json.dumps(message) for message in messages) + "\n")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

        def kill(self):
            return None

    monkeypatch.setattr("kogwistar_llm_wiki.codex_workbench_agent.subprocess.Popen", FakeProcess)
    progress: list[str] = []
    traces: list[str] = []
    raw = CodexAppServerRunner().run(
        settings=CodexCliSettings(executable="codex", model="test-model", profile="safe"),
        prompt="return a grounded answer",
        progress=lambda: progress.append("tick"),
        trace_line=traces.append,
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    )

    assert raw == "Hello world"
    assert len(progress) == len(messages)
    assert traces == [json.dumps(message) for message in messages]
    command = captured["command"]
    assert command[-2:] == ["app-server", "--stdio"]
    sent = captured["messages"]
    assert sent[0]["method"] == "initialize"
    assert sent[1] == {"jsonrpc": "2.0", "method": "initialized"}
    assert sent[2]["method"] == "thread/start"
    assert sent[2]["params"]["sandbox"] == "read-only"
    assert sent[2]["params"]["approvalPolicy"] == "never"
    assert sent[2]["params"]["model"] == "test-model"
    assert sent[2]["params"]["config"] == {"profile": "safe"}
    assert sent[3]["method"] == "turn/start"
    assert sent[3]["params"]["threadId"] == "thread-1"
    assert sent[3]["params"]["outputSchema"]["required"] == ["answer"]


def test_app_server_transport_selects_the_runner_for_responder():
    responder = CodexCliResponder(CodexCliSettings(transport="app_server"))

    assert isinstance(responder.runner, CodexAppServerRunner)


def test_app_server_runner_terminates_child_after_protocol_error(monkeypatch):
    state = {"terminated": False}

    class FakeProcess:
        def __init__(self, _command, **_kwargs):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {
                            "code": -32000,
                            "error": {"type": "invalid_request_error", "message": "bad initialize"},
                        },
                    }
                )
                + "\n"
            )

        def poll(self):
            return 0 if state["terminated"] else None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            state["terminated"] = True

        def kill(self):
            state["terminated"] = True

    monkeypatch.setattr("kogwistar_llm_wiki.codex_workbench_agent.subprocess.Popen", FakeProcess)

    with pytest.raises(RuntimeError, match="initialize failed"):
        CodexAppServerRunner().run(
            settings=CodexCliSettings(executable="codex", timeout_seconds=1),
            prompt="hello",
            progress=lambda: None,
        )

    assert state["terminated"] is True


def test_app_server_runner_fails_immediately_on_terminal_error_notification(monkeypatch):
    class FakeProcess:
        def __init__(self, _command, **_kwargs):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                "\n".join(
                    [
                        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
                        json.dumps({"method": "error", "params": {"willRetry": False, "error": {"message": "unauthorized"}}}),
                    ]
                )
                + "\n"
            )

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

        def kill(self):
            return None

    monkeypatch.setattr("kogwistar_llm_wiki.codex_workbench_agent.subprocess.Popen", FakeProcess)

    with pytest.raises(RuntimeError, match="terminal error: unauthorized"):
        CodexAppServerRunner().run(
            settings=CodexCliSettings(executable="codex"),
            prompt="hello",
            progress=lambda: None,
        )


def test_cockpit_schema_is_strict_at_every_object_boundary():
    schema = _strict_output_schema(CockpitAction.model_json_schema())

    def assert_strict(value):
        if isinstance(value, list):
            for item in value:
                assert_strict(item)
            return
        if not isinstance(value, dict):
            return
        if isinstance(value.get("properties"), dict):
            assert set(value["required"]) == set(value["properties"])
            assert value["additionalProperties"] is False
        elif value.get("type") == "object":
            assert value["additionalProperties"] is False
        assert "default" not in value
        for nested in (value.get("$defs") or {}).values():
            assert_strict(nested)
        for nested in (value.get("properties") or {}).values():
            assert_strict(nested)
        assert_strict(value.get("items"))
        for key in ("anyOf", "allOf", "oneOf"):
            assert_strict(value.get(key))

    assert_strict(schema)
    assert "rationale" in schema["required"]


def test_real_cockpit_transport_avoids_open_patch_maps():
    schema = _cockpit_transport_schema()

    assert schema["required"] == [
        "kind",
        "answer",
        "query_text",
        "entity_ids",
        "patch_json",
        "cited_entity_ids",
        "rationale",
    ]
    assert schema["additionalProperties"] is False
    assert "patch" not in schema["properties"]
    assert schema["properties"]["patch_json"]["anyOf"][-1] == {"type": "null"}


def test_host_cockpit_responder_requires_allowlisted_endpoint():
    HostCockpitResponder(
        "http://host.docker.internal:9000/cockpit",
        allowed_hosts=["host.docker.internal"],
    )
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        HostCockpitResponder(
            "http://unexpected.example/cockpit",
            allowed_hosts=["host.docker.internal"],
        )


def _snapshot() -> SemanticLensSnapshot:
    return SemanticLensSnapshot(
        lens_id="lens-1",
        workspace_id="agent-test",
        source_watermark=7,
        projected_at_ms=1,
        completeness="bounded",
        nodes=(),
        edges=(),
        hyperedges=(),
        participations=(),
        anchor_explanations=(),
        selection_explanations=(),
        omitted_summary={},
        query_timing_ms=1,
    )
