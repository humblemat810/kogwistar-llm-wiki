from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from kogwistar.runtime.sinks import JsonlEventSink

from .debug_run import LiveTracePrinter


class LongRunJsonlTraceSink(JsonlEventSink):
    def __init__(
        self,
        *,
        jsonl_path: Path,
        downstream_sink: Any | None = None,
        enrich_event: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        live_trace: bool = False,
    ) -> None:
        super().__init__(jsonl_path=jsonl_path, downstream_sink=downstream_sink)
        self.enrich_event = enrich_event
        self.live_trace_printer = LiveTracePrinter(prefix="longrun.runtime") if live_trace else None

    def emit(self, event: dict[str, Any]) -> None:
        enriched_event = self.enrich_event(dict(event)) if self.enrich_event is not None else dict(event)
        super().emit(enriched_event)
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(enriched_event)


__all__ = ["LongRunJsonlTraceSink"]
