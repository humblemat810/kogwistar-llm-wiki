"""Streaming Kogwistar provider adapter for the signed-in Codex App Server."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol, cast

from kogwistar.agent import (
    IncrementalJsonObjectParser,
    ProviderCapabilities,
    ProviderEvent,
    ProviderFieldCompleted,
    ProviderRequest,
    ProviderResponseCompleted,
    ProviderResponseFailed,
    ProviderResponseStarted,
    ProviderTextDelta,
    StructuredOutputError,
)
from kogwistar.json_types import JsonValue


class CodexAppServerSession(Protocol):
    async def request(self, method: str, params: Mapping[str, object]) -> Mapping[str, object]: ...

    async def notify(self, method: str, params: Mapping[str, object] | None = None) -> None: ...

    def events(self) -> AsyncIterator[Mapping[str, object]]: ...

    async def close(self) -> None: ...


class _SubprocessCodexSession:
    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.process: asyncio.subprocess.Process | None = None
        self._incoming: asyncio.Queue[Mapping[str, object] | None] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._stderr_reader: asyncio.Task[None] | None = None
        self._request_id = 0
        self._buffered_events: list[Mapping[str, object]] = []

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.executable,
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr_reader = asyncio.create_task(self._drain_stderr())

    async def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    await self._incoming.put({"method": "error", "params": {"error": {"message": "Codex App Server returned invalid JSON"}}})
                    continue
                if isinstance(message, Mapping):
                    await self._incoming.put(cast(Mapping[str, object], message))
        finally:
            await self._incoming.put(None)

    async def _drain_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        while await process.stderr.readline():
            pass

    async def _send(self, message: Mapping[str, object]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise RuntimeError("Codex App Server process is not running")
        process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await process.stdin.drain()

    async def request(self, method: str, params: Mapping[str, object]) -> Mapping[str, object]:
        self._request_id += 1
        request_id = self._request_id
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)})
        while True:
            message = await self._next_message()
            if message.get("id") != request_id:
                if "method" in message:
                    self._buffered_events.append(message)
                continue
            error = message.get("error")
            if isinstance(error, Mapping):
                details = error.get("message")
                raise RuntimeError(  # noqa: TRY004 - remote RPC failure, not a local type error
                    f"Codex App Server {method} failed: {str(details or 'unknown error')[:300]}"
                )
            result = message.get("result")
            if not isinstance(result, Mapping):
                raise TypeError(f"Codex App Server {method} returned no object result")
            return cast(Mapping[str, object], result)

    async def notify(self, method: str, params: Mapping[str, object] | None = None) -> None:
        message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        await self._send(message)

    async def _next_message(self) -> Mapping[str, object]:
        message = await self._incoming.get()
        if message is None:
            raise RuntimeError("Codex App Server closed its JSON-RPC stream")
        return message

    async def events(self) -> AsyncIterator[Mapping[str, object]]:
        while self._buffered_events:
            yield self._buffered_events.pop(0)
        while True:
            message = await self._next_message()
            if "method" in message:
                yield message

    async def close(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                process.stdin.close()
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader, self._stderr_reader):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


class CodexAppServerProvider:
    """Normalize Codex App Server output; Codex tools are not Kogwistar tools."""

    provider_id = "openai.codex_app_server"
    capabilities = ProviderCapabilities(
        structured_output=True,
        tool_calls=False,
        reasoning_summaries=False,
        steering=False,
        cancellation=False,
    )

    def __init__(
        self,
        *,
        executable: str | None = None,
        profile: str | None = None,
        timeout_seconds: float = 300.0,
        max_structured_chars: int = 64_000,
        session_factory: Callable[[], CodexAppServerSession] | None = None,
    ) -> None:
        self.executable = str(executable or os.getenv("KOGWISTAR_CODEX_EXECUTABLE") or "codex")
        self.profile = profile or os.getenv("KOGWISTAR_CODEX_PROFILE")
        self.timeout_seconds = float(timeout_seconds)
        self.max_structured_chars = int(max_structured_chars)
        self._session_factory = session_factory
        if self.timeout_seconds <= 0 or self.max_structured_chars < 2:
            raise ValueError("timeout must be positive and structured output limit at least 2")

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderEvent]:
        factory = self._session_factory
        session = factory() if factory is not None else _SubprocessCodexSession(self.executable)
        parser = (
            IncrementalJsonObjectParser(max_chars=self.max_structured_chars)
            if request.response_schema is not None
            else None
        )
        response_id: str | None = None
        sequence: int | None = None
        try:
            start = getattr(session, "start", None)
            if callable(start):
                await cast(Awaitable[None], start())
            async with asyncio.timeout(self.timeout_seconds):
                async for event in self._run_session(session, request, parser):
                    if event.response_id:
                        response_id = event.response_id
                    yield event
        except TimeoutError:
            yield ProviderResponseFailed(
                response_id=response_id,
                error_code="timeout",
                message="Codex App Server response exceeded the configured timeout",
                retryable=True,
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider/transport failures into events
            safe_message = (
                str(exc)[:300]
                if isinstance(exc, StructuredOutputError)
                else "Codex App Server streaming request failed"
            )
            yield ProviderResponseFailed(
                response_id=response_id,
                provider_sequence=sequence,
                error_code=type(exc).__name__.lower(),
                message=safe_message,
            )
        finally:
            await session.close()

    async def _run_session(
        self,
        session: CodexAppServerSession,
        request: ProviderRequest,
        parser: IncrementalJsonObjectParser | None,
    ) -> AsyncIterator[ProviderEvent]:
        """Run one ephemeral read-only Codex thread and normalize its events."""
        await session.request(
            "initialize",
            {
                "clientInfo": {"name": "kogwistar", "version": "stream-v1"},
                "capabilities": {},
            },
        )
        await session.notify("initialized")
        thread_params: dict[str, object] = {
            "cwd": str(Path.cwd()),
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "model": request.model,
        }
        if self.profile:
            thread_params["config"] = {"profile": self.profile}
        thread_result = await session.request("thread/start", thread_params)
        thread = thread_result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("Codex App Server thread/start returned no thread id")

        prompt = request.input
        if request.instructions:
            prompt = f"{request.instructions}\n\n{request.input}"
        turn_params: dict[str, object] = {
            "threadId": thread_id,
            "model": request.model,
            "input": [{"type": "text", "text": prompt}],
        }
        if request.response_schema is not None:
            turn_params["outputSchema"] = request.response_schema
        turn_result = await session.request("turn/start", turn_params)
        turn = turn_result.get("turn")
        turn_id = turn.get("id") if isinstance(turn, Mapping) else None
        response_id = str(turn_id or "codex-turn")
        yield ProviderResponseStarted(response_id=response_id)

        sequence = 0
        text_parts: list[str] = []
        saw_delta = False
        async for message in session.events():
            method = message.get("method")
            params = message.get("params")
            if not isinstance(params, Mapping):
                continue
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    saw_delta = True
                    text_parts.append(delta)
                    sequence += 1
                    yield ProviderTextDelta(
                        provider_sequence=sequence,
                        response_id=response_id,
                        delta=delta,
                    )
            elif method == "item/completed":
                item = params.get("item")
                if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str):
                        if saw_delta:
                            # App Server deltas are provisional; the completed item
                            # is authoritative and may include a final suffix.
                            if len(text) >= len("".join(text_parts)):
                                text_parts[:] = [text]
                        else:
                            text_parts.append(text)
                            sequence += 1
                            yield ProviderTextDelta(
                                provider_sequence=sequence,
                                response_id=response_id,
                                delta=text,
                            )
            elif method == "error":
                if params.get("willRetry") is not True:
                    yield ProviderResponseFailed(
                        provider_sequence=sequence + 1,
                        response_id=response_id,
                        error_code="codex_app_server_error",
                    message="Codex App Server reported a terminal error",
                    )
                    return
            elif method == "turn/completed" and params.get("threadId") == thread_id:
                completed_turn = params.get("turn")
                if not isinstance(completed_turn, Mapping):
                    raise RuntimeError("Codex App Server completion omitted turn data")
                status = completed_turn.get("status")
                if status != "completed":
                    yield ProviderResponseFailed(
                        provider_sequence=sequence + 1,
                        response_id=response_id,
                        error_code="codex_turn_incomplete",
                        message="Codex turn did not complete",
                    )
                    return
                structured = None
                if parser is not None:
                    final_text = "".join(text_parts)
                    for key, value in parser.feed(final_text):
                        sequence += 1
                        yield ProviderFieldCompleted(
                            provider_sequence=sequence,
                            response_id=response_id,
                            field_name=key,
                            value=cast(JsonValue, value),
                        )
                    structured = parser.finish()
                sequence += 1
                yield ProviderResponseCompleted(
                    provider_sequence=sequence,
                    response_id=response_id,
                    text="".join(text_parts),
                    structured_output=structured,
                )
                return
        raise RuntimeError("Codex App Server closed before turn/completed")


__all__ = ["CodexAppServerProvider", "CodexAppServerSession"]
