from __future__ import annotations

import base64
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from kg_doc_parser.workflow_ingest.providers import (
    CodexBridgeChatModel,
    ProviderChainChatModel,
    _codex_messages,
)
from kogwistar.llm_tasks.providers import (
    ProviderChainChatModel as SharedProviderChainChatModel,
)
from kogwistar.llm_tasks.providers import (
    StructuredBridgeChatModel,
    bridge_messages,
)
from pydantic import BaseModel

from kogwistar_llm_wiki.codex.codex_bridge import CodexBridgeState
from kogwistar_llm_wiki.codex.codex_workbench_agent import (
    CodexCliSettings,
    _parse_cockpit_action,
)
from kogwistar_llm_wiki.providers.role_config import (
    resolve_maintenance_provider_settings,
)


class Answer(BaseModel):
    text: str


@dataclass
class FakeRunner:
    value: str = '{"text":"bounded"}'

    def run(self, **kwargs: Any) -> str:
        assert kwargs["output_schema"]["type"] == "object"
        assert "Do not call tools" in kwargs["prompt"]
        return self.value


def test_bridge_rejects_empty_token_and_bounds_context() -> None:
    state = CodexBridgeState(token="secret", settings=CodexCliSettings())
    state.runner = FakeRunner()
    with pytest.raises(ValueError, match="between 1 and 64"):
        state.complete({"messages": [], "response_schema": {}})
    with pytest.raises(ValueError, match="context exceeds"):
        state.complete({"messages": [{"role": "user", "content": "x" * 120001}], "response_schema": {}})


def test_bridge_returns_only_valid_structured_object() -> None:
    state = CodexBridgeState(token="secret", settings=CodexCliSettings(model="codex-test"))
    state.runner = FakeRunner()
    result = state.complete(
        {"messages": [{"role": "user", "content": "summarize"}], "response_schema": Answer.model_json_schema()}
    )
    assert result == {"output": {"text": "bounded"}, "provider": "codex", "model": "codex-test"}


def test_bridge_forwards_data_image_to_codex_runner() -> None:
    captured: dict[str, object] = {}

    class ImageRunner:
        def run(self, **kwargs: object) -> str:
            captured.update(kwargs)
            images = kwargs["images"]
            assert isinstance(images, list) and len(images) == 1
            assert images[0].read_bytes() == b"fake-png"
            return '{"text":"image seen"}'

    state = CodexBridgeState(token="secret", settings=CodexCliSettings(model="vision-codex"))
    state.runner = ImageRunner()
    data_url = "data:image/png;base64," + base64.b64encode(b"fake-png").decode("ascii")
    result = state.complete(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "read this page"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "response_schema": Answer.model_json_schema(),
        }
    )
    assert result["output"] == {"text": "image seen"}
    assert "[image attached]" in str(captured["prompt"])


def test_bridge_rejects_remote_image_urls() -> None:
    state = CodexBridgeState(token="secret", settings=CodexCliSettings())
    with pytest.raises(ValueError, match="base64 data-image"):
        state.complete(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "https://example.invalid/page.png"}}
                        ],
                    }
                ],
                "response_schema": Answer.model_json_schema(),
            }
        )


def test_bridge_rejects_unsupported_image_formats() -> None:
    state = CodexBridgeState(token="token", settings=CodexCliSettings())
    encoded = base64.b64encode(b"fake-svg").decode("ascii")
    with pytest.raises(ValueError, match="unsupported Codex image format"):
        state.complete(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/svg+xml;base64,{encoded}"},
                            }
                        ],
                    }
                ],
                "response_schema": {"type": "object"},
            }
        )


def test_codex_action_parser_rejects_unrelated_free_form_json() -> None:
    with pytest.raises(ValueError, match="invalid cockpit transport"):
        _parse_cockpit_action('{"semantic_boundaries": {"blocks": []}}')


def test_codex_messages_use_openai_roles_without_paths() -> None:
    message = SimpleNamespace(type="human", content="file:///secret/path")
    assert _codex_messages([message]) == [{"role": "user", "content": "file:///secret/path"}]
    assert _codex_messages is bridge_messages
    assert CodexBridgeChatModel is StructuredBridgeChatModel


def test_codex_messages_preserve_image_content_parts() -> None:
    content = [
        {"type": "text", "text": "inspect"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZmFrZQ=="}},
    ]
    message = SimpleNamespace(type="human", content=content)
    assert _codex_messages([message]) == [{"role": "user", "content": content}]


def test_provider_chain_falls_back_only_on_retryable_error() -> None:
    class Retryable:
        def with_structured_output(self, schema: type[BaseModel], include_raw: bool = True) -> Any:
            _ = schema, include_raw
            return SimpleNamespace(invoke=lambda messages, config=None: (_ for _ in ()).throw(TimeoutError("offline")))

    class Successful:
        def with_structured_output(self, schema: type[BaseModel], include_raw: bool = True) -> Any:
            _ = include_raw
            return SimpleNamespace(invoke=lambda messages, config=None: {"parsed": schema(text="ok")})

    assert ProviderChainChatModel is SharedProviderChainChatModel
    chain = ProviderChainChatModel.__new__(ProviderChainChatModel)
    chain.models = [("codex", Retryable()), ("ollama", Successful())]
    result = chain.with_structured_output(Answer).invoke([])
    assert result["provider"] == "ollama"
    assert result["fallback_from"] == "codex"


def test_provider_chain_does_not_fallback_on_validation_error() -> None:
    class Terminal:
        def with_structured_output(self, schema: type[BaseModel], include_raw: bool = True) -> Any:
            _ = schema, include_raw
            return SimpleNamespace(invoke=lambda messages, config=None: (_ for _ in ()).throw(ValueError("unauthorized")))

    chain = ProviderChainChatModel.__new__(ProviderChainChatModel)
    chain.models = [("codex", Terminal()), ("ollama", object())]
    with pytest.raises(ValueError, match="unauthorized"):
        chain.with_structured_output(Answer).invoke([])


def test_maintenance_chain_is_opt_in_and_provider_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOGWISTAR_MAINTENANCE_PROVIDER_CHAIN", "codex,ollama")
    monkeypatch.setenv("LLM_WIKI_CODEX_BRIDGE_TOKEN", "bridge-token")
    monkeypatch.setenv("KOGWISTAR_MAINTENANCE_CODEX_API_KEY_ENV", "LLM_WIKI_CODEX_BRIDGE_TOKEN")
    settings = resolve_maintenance_provider_settings()
    assert settings.parser.provider == "codex"
    assert [item.provider for item in settings.parser.fallback_specs] == ["ollama"]
    assert settings.parser.fallback_specs[0].base_url == "http://host.docker.internal:11434"
