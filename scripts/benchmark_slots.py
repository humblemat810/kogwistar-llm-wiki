"""Measure migrated dataclass layouts against equivalent unslotted layouts.

This is a manual benchmark. Run it with the same interpreter before and after
slot changes; do not use its platform-specific byte counts as a CI contract.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import platform
import sys
import threading
import time
from dataclasses import MISSING, field, fields, make_dataclass
from pathlib import Path
from typing import Any

from kogwistar.conversation.conversation_context import ContextMessage, DroppedItem
from kogwistar.conversation.retrieval_orchestrator import RetrievalOutcome
from kogwistar.messaging.models import (
    LaneMessageLookup,
    LaneMessageProjectionRepairResult,
    LaneMessageSendResult,
)

from kogwistar_llm_wiki.codex.codex_bridge import CodexBridgeState
from kogwistar_llm_wiki.codex.codex_compose_tui import LaunchStep, TuiConfiguration
from kogwistar_llm_wiki.codex.codex_memory import CodexMemoryService
from kogwistar_llm_wiki.codex.codex_workbench_agent import (
    CodexCliSettings,
    CodexProcessRunner,
)
from kogwistar_llm_wiki.compose.options import ComposeOptions
from kogwistar_llm_wiki.configuration.identity import LlmWikiIdentity
from kogwistar_llm_wiki.diagnostics.debug_helpers import LiveTracePrinter
from kogwistar_llm_wiki.embeddings.multimodal_sources import MappingAssetResolver
from kogwistar_llm_wiki.maintenance.maintenance_strategies import (
    MaintenanceJobExecutionContext,
    MaintenanceStrategyRegistry,
)
from kogwistar_llm_wiki.workbench.review_query import ReviewQueryService
from kogwistar_llm_wiki.workbench.semantic_lens import SemanticLensService
from kogwistar_llm_wiki.workbench.workbench_background import WorkbenchInteractionStore

try:
    import tracemalloc
except ImportError:  # PyPy builds may omit the optional CPython tracer.
    tracemalloc = None  # type: ignore[assignment]


def _legacy_dataclass(current_type: type[Any]) -> type[Any]:
    """Create an equivalent unslotted dataclass for a layout comparison."""
    definitions: list[tuple[str, type[Any], Any]] = []
    for item in fields(current_type):
        if item.default is not MISSING:
            default: Any = field(default=item.default)
        elif item.default_factory is not MISSING:  # type: ignore[comparison-overlap]
            default = field(default_factory=item.default_factory)
        else:
            default = field()
        definitions.append((item.name, object, default))
    return make_dataclass(
        f"Unslotted{current_type.__name__}",
        definitions,
        frozen=True,
    )


def _sample_values() -> dict[type[Any], tuple[Any, ...]]:
    return {
        ContextMessage: ("user", "benchmark", "node-1", "live_turn"),
        DroppedItem: ("tail_turn", "node-2", "over_budget", 12),
        RetrievalOutcome: (object(), object(), None, ["node-1"], ["edge-1"]),
        LaneMessageSendResult: (
            "message-1",
            "conversation-1",
            "inbox-1",
            "sender-1",
            "recipient-1",
        ),
        LaneMessageProjectionRepairResult: ("demo", 3, 2, 1, False),
        LaneMessageLookup: (),
        ComposeOptions: (),
        LaunchStep: ("benchmark", ["echo", "ok"]),
        TuiConfiguration: (),
        LlmWikiIdentity: (
            "benchmark",
            frozenset({"read"}),
            "ro",
            "benchmark",
            frozenset({"demo"}),
            {},
            "disabled",
        ),
        MaintenanceJobExecutionContext: (
            "demo",
            object(),
            "job-1",
            {},
            None,
            "node-1",
            "message-1",
            "maintenance",
        ),
    }


def _legacy_codex_bridge_state() -> type[Any]:
    """Create an equivalent unslotted bridge state for the layout baseline."""

    class UnslottedCodexBridgeState:
        def __init__(self, *, token: str, settings: CodexCliSettings) -> None:
            self.token = token
            self.settings = settings
            self.runner = CodexProcessRunner()
            self.lock = threading.Lock()

    return UnslottedCodexBridgeState


def _legacy_fixed_state_type(values: dict[str, object]) -> type[Any]:
    """Create an unslotted fixed-state baseline with the same fields."""

    def copy_value(value: object) -> object:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        return value

    class UnslottedFixedState:
        def __init__(self) -> None:
            self.__dict__.update({key: copy_value(value) for key, value in values.items()})

    return UnslottedFixedState


def _measure(factory: Any, count: int, warmup_count: int) -> dict[str, float | int]:
    gc.collect()
    for _ in range(warmup_count):
        factory()
    rss_before = _rss_bytes()
    if tracemalloc is not None:
        tracemalloc.start()
    started = time.perf_counter()
    started_cpu = time.process_time()
    values = [factory() for _ in range(count)]
    elapsed = time.perf_counter() - started
    cpu_elapsed = time.process_time() - started_cpu
    current = peak = None
    if tracemalloc is not None:
        current, peak = tracemalloc.get_traced_memory()
    rss_after = _rss_bytes()
    del values
    if tracemalloc is not None:
        tracemalloc.stop()
    result: dict[str, float | int] = {
        "count": count,
        "seconds": elapsed,
        "microseconds_per_instance": elapsed * 1_000_000 / count,
        "cpu_seconds": cpu_elapsed,
        "cpu_microseconds_per_instance": cpu_elapsed * 1_000_000 / count,
        "cpu_utilization_percent": cpu_elapsed / max(elapsed, 1e-12) * 100.0,
        "peak_bytes": peak,
        "current_bytes_before_release": current,
    }
    if rss_before is not None:
        result["rss_before_bytes"] = rss_before
    if rss_after is not None:
        result["rss_after_bytes"] = rss_after
        if rss_before is not None:
            result["rss_delta_bytes"] = rss_after - rss_before
    return result


def _rss_bytes() -> int | None:
    """Return best-effort resident memory without adding a benchmark dependency."""
    if sys.platform == "win32":
        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        if psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return int(counters.WorkingSetSize)
        return None
    if sys.platform == "linux":
        try:
            with Path("/proc/self/statm").open(encoding="ascii") as stream:
                resident_pages = int(stream.read().split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, IndexError, ValueError):
            return None
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value * (1024 if platform.system() == "Linux" else 1))


def _host_environment() -> str:
    """Return the environment boundary used for fair runtime comparisons."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "linux":
        try:
            proc_version = Path("/proc/version").read_text(encoding="utf-8").lower()
        except OSError:
            proc_version = ""
        if "microsoft" in proc_version or "wsl" in proc_version or "WSL_INTEROP" in os.environ:
            return "wsl"
        return "linux"
    return platform.system().lower()


def _factory(cls: type[Any], args: tuple[Any, ...]) -> Any:
    def create() -> Any:
        return cls(*args)

    return create


def _factory_with_kwargs(cls: type[Any], *args: Any, **kwargs: Any) -> Any:
    def create() -> Any:
        return cls(*args, **kwargs)

    return create


def _benchmark_clock_ms() -> int:
    return 0


def _layout_sizes(factory: Any) -> dict[str, int]:
    value = factory()
    result: dict[str, int] = {}
    try:
        result["instance_size"] = sys.getsizeof(value)
        if hasattr(value, "__dict__"):
            result["instance_dict_size"] = sys.getsizeof(value.__dict__)
    except TypeError:
        # PyPy deliberately does not expose CPython-style per-object sizes.
        pass
    return result


def _require_strict_slots(name: str, factory: Any) -> None:
    error = _strict_slot_error(name, factory)
    if error is not None:
        raise RuntimeError(error)


def _strict_slot_error(name: str, factory: Any) -> str | None:
    value = factory()
    if hasattr(value, "__dict__"):
        return (
            f"{name} is not strictly slotted in the pinned dependency revision; "
            "advance the dependency pin after its slot change is merged"
        )
    return None


_OPTIONAL_VENDOR_SLOT_TYPES = frozenset(
    {
        LaneMessageSendResult,
        LaneMessageProjectionRepairResult,
        LaneMessageLookup,
    }
)


def run(
    *, counts: tuple[int, ...], warmup_count: int = 0
) -> tuple[dict[str, Any], dict[str, str]]:
    results: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    for current_type, args in _sample_values().items():
        legacy_type = _legacy_dataclass(current_type)
        current_factory = _factory(current_type, args)
        legacy_factory = _factory(legacy_type, args)
        if current_type in _OPTIONAL_VENDOR_SLOT_TYPES:
            error = _strict_slot_error(current_type.__name__, current_factory)
            if error is not None:
                skipped[current_type.__name__] = error
                continue
        _require_strict_slots(current_type.__name__, current_factory)
        results[current_type.__name__] = {
            "slotted": {
                **_layout_sizes(current_factory),
                "measurements": [
                    _measure(current_factory, count, warmup_count) for count in counts
                ],
            },
            "unslotted_equivalent": {
                **_layout_sizes(legacy_factory),
                "measurements": [
                    _measure(legacy_factory, count, warmup_count) for count in counts
                ],
            },
        }
    bridge_args = {"token": "benchmark", "settings": CodexCliSettings()}
    legacy_bridge_type = _legacy_codex_bridge_state()

    def bridge_factory() -> CodexBridgeState:
        return CodexBridgeState(**bridge_args)

    def legacy_bridge_factory() -> Any:
        return legacy_bridge_type(**bridge_args)

    _require_strict_slots(CodexBridgeState.__name__, bridge_factory)
    results[CodexBridgeState.__name__] = {
        "slotted": {
            **_layout_sizes(bridge_factory),
            "measurements": [
                _measure(bridge_factory, count, warmup_count) for count in counts
            ],
        },
        "unslotted_equivalent": {
            **_layout_sizes(legacy_bridge_factory),
            "measurements": [
                _measure(legacy_bridge_factory, count, warmup_count) for count in counts
            ],
        },
    }
    fixed_states = {
        CodexMemoryService.__name__: (
            _factory_with_kwargs(CodexMemoryService, object(), enabled=False),
            _factory(_legacy_fixed_state_type({"engines": object(), "enabled": False, "max_records_per_capture": 8, "max_recall_records": 12}), ()),
        ),
        SemanticLensService.__name__: (
            _factory_with_kwargs(
                SemanticLensService,
                object(),
                query_service=object(),
                clock_ms=_benchmark_clock_ms,
            ),
            _factory(
                _legacy_fixed_state_type(
                    {"engines": object(), "query_service": object(), "_clock_ms": _benchmark_clock_ms}
                ),
                (),
            ),
        ),
        ReviewQueryService.__name__: (
            _factory(ReviewQueryService, (object(),)),
            _factory(_legacy_fixed_state_type({"engines": object()}), ()),
        ),
        WorkbenchInteractionStore.__name__: (
            _factory(WorkbenchInteractionStore, (object(),)),
            _factory(_legacy_fixed_state_type({"engines": object()}), ()),
        ),
        MaintenanceStrategyRegistry.__name__: (
            _factory(MaintenanceStrategyRegistry, ()),
            _factory(_legacy_fixed_state_type({"_strategies": []}), ()),
        ),
        LiveTracePrinter.__name__: (
            _factory_with_kwargs(LiveTracePrinter, prefix="benchmark"),
            _factory(_legacy_fixed_state_type({"prefix": "benchmark"}), ()),
        ),
        MappingAssetResolver.__name__: (
            _factory(MappingAssetResolver, ({},)),
            _factory(_legacy_fixed_state_type({"_assets": {}}), ()),
        ),
    }
    for name, (current_factory, legacy_factory) in fixed_states.items():
        _require_strict_slots(name, current_factory)
        results[name] = {
            "slotted": {
                **_layout_sizes(current_factory),
                "measurements": [
                    _measure(current_factory, count, warmup_count) for count in counts
                ],
            },
            "unslotted_equivalent": {
                **_layout_sizes(legacy_factory),
                "measurements": [
                    _measure(legacy_factory, count, warmup_count) for count in counts
                ],
            },
        }
    return results, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, action="append", dest="counts")
    parser.add_argument(
        "--warmup-count",
        type=int,
        default=0,
        help="Instances to construct before each measurement; use this to warm PyPy's JIT.",
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    counts = tuple(args.counts or (100, 10_000, 100_000))
    if any(count <= 0 for count in counts):
        parser.error("--count values must be positive")
    if args.warmup_count < 0:
        parser.error("--warmup-count must not be negative")
    results, skipped = run(counts=counts, warmup_count=args.warmup_count)
    report = {
        "python": __import__("sys").version,
        "implementation": __import__("platform").python_implementation(),
        "python_executable": str(Path(sys.executable).resolve()),
        "host_environment": _host_environment(),
        "host_system": platform.system(),
        "host_release": platform.release(),
        "host_machine": platform.machine(),
        "tracemalloc_available": tracemalloc is not None,
        "counts": counts,
        "warmup_count": args.warmup_count,
        "results": results,
        "skipped": skipped,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out is None:
        print(rendered, end="")
    else:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
