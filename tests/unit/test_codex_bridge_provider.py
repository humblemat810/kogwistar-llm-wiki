from __future__ import annotations

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
from kogwistar_llm_wiki.codex.codex_workbench_agent import CodexCliSettings
from kogwistar_llm_wiki.provider_config import resolve_maintenance_provider_settings


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


def test_codex_messages_use_openai_roles_without_paths() -> None:
    message = SimpleNamespace(type="human", content="file:///secret/path")
    assert _codex_messages([message]) == [{"role": "user", "content": "file:///secret/path"}]
    assert _codex_messages is bridge_messages
    assert CodexBridgeChatModel is StructuredBridgeChatModel


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
