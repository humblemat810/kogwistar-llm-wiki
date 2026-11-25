from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

from kogwistar.agent.streaming import (
    ProviderEvent,
    ProviderFieldCompleted,
    ProviderRequest,
    ProviderResponseCompleted,
    ProviderResponseFailed,
    ProviderTextDelta,
)

from kogwistar_llm_wiki.codex.streaming_provider import CodexAppServerProvider


class FakeCodexSession:
    def __init__(self, events: list[Mapping[str, object]]) -> None:
        self._events = events
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.closed = False

    async def request(self, method: str, params: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    async def notify(self, method: str, params: Mapping[str, object] | None = None) -> None:
        self.calls.append((method, params or {}))

    async def events(self) -> AsyncIterator[Mapping[str, object]]:
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


def _collect(provider: CodexAppServerProvider, request: ProviderRequest) -> list[ProviderEvent]:
    async def run() -> list[ProviderEvent]:
        return [event async for event in provider.stream(request)]

    return asyncio.run(run())


def test_codex_adapter_streams_and_parses_complete_structured_output() -> None:
    session = FakeCodexSession(
        [
            {"method": "item/agentMessage/delta", "params": {"delta": '{"answer":"provisional"}' }},
            {
                "method": "item/completed",
                "params": {
                    "item": {"type": "agentMessage", "text": '{"answer":"authoritative"}'}
                },
            },
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
            },
        ]
    )
    provider = CodexAppServerProvider(session_factory=lambda: session)
    events = _collect(
        provider,
        ProviderRequest(model="test-model", input="Return structured output", response_schema={"type": "object"}),
    )

    assert any(isinstance(event, ProviderTextDelta) for event in events)
    field_events = [event for event in events if isinstance(event, ProviderFieldCompleted)]
    assert len(field_events) == 1
    assert field_events[0].field_name == "answer"
    assert field_events[0].value == "authoritative"
    last_delta = max(i for i, event in enumerate(events) if isinstance(event, ProviderTextDelta))
    assert events.index(field_events[0]) > last_delta
    completed = next(event for event in events if isinstance(event, ProviderResponseCompleted))
    assert completed.structured_output == {"answer": "authoritative"}
    assert session.closed
    thread_start = next(params for method, params in session.calls if method == "thread/start")
    assert thread_start["ephemeral"] is True
    assert thread_start["sandbox"] == "read-only"
    assert thread_start["approvalPolicy"] == "never"


def test_codex_adapter_fails_closed_for_invalid_or_incomplete_schema() -> None:
    session = FakeCodexSession(
        [
            {"method": "item/agentMessage/delta", "params": {"delta": '{"answer":'}},
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turn": {"status": "completed"}},
            },
        ]
    )
    provider = CodexAppServerProvider(session_factory=lambda: session)
    events = _collect(
        provider,
        ProviderRequest(model="test-model", input="Return JSON", response_schema={"type": "object"}),
    )
    assert isinstance(events[-1], ProviderResponseFailed)
    assert session.closed


def test_codex_adapter_does_not_claim_kogwistar_tool_execution() -> None:
    assert CodexAppServerProvider.capabilities.tool_calls is False
