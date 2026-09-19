"""Measure migrated dataclass layouts against equivalent unslotted layouts.

This is a manual benchmark. Run it with the same interpreter before and after
slot changes; do not use its platform-specific byte counts as a CI contract.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import platform
import sys
import time
import tracemalloc
from dataclasses import MISSING, field, fields, make_dataclass
from pathlib import Path
from typing import Any

from kogwistar_llm_wiki.codex.codex_compose_tui import LaunchStep, TuiConfiguration
from kogwistar_llm_wiki.compose.options import ComposeOptions
from kogwistar_llm_wiki.configuration.identity import LlmWikiIdentity
from kogwistar_llm_wiki.maintenance.maintenance_strategies import (
    MaintenanceJobExecutionContext,
)


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


def _measure(factory: Any, count: int, warmup_count: int) -> dict[str, float | int]:
    gc.collect()
    for _ in range(warmup_count):
        factory()
    rss_before = _rss_bytes()
    tracemalloc.start()
    started = time.perf_counter()
    started_cpu = time.process_time()
    values = [factory() for _ in range(count)]
    elapsed = time.perf_counter() - started
    cpu_elapsed = time.process_time() - started_cpu
    current, peak = tracemalloc.get_traced_memory()
    rss_after = _rss_bytes()
    del values
    tracemalloc.stop()
    result: dict[str, float | int] = {
        "count": count,
        "seconds": elapsed,
        "microseconds_per_instance": elapsed * 1_000_000 / count,
        "cpu_seconds": cpu_elapsed,
        "cpu_microseconds_per_instance": cpu_elapsed * 1_000_000 / count,
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
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value * (1024 if platform.system() == "Linux" else 1))


def _factory(cls: type[Any], args: tuple[Any, ...]) -> Any:
    def create() -> Any:
        return cls(*args)

    return create


def _layout_sizes(cls: type[Any], args: tuple[Any, ...]) -> dict[str, int]:
    value = cls(*args)
    result = {"instance_size": sys.getsizeof(value)}
    if hasattr(value, "__dict__"):
        result["instance_dict_size"] = sys.getsizeof(value.__dict__)
    return result


def run(*, counts: tuple[int, ...], warmup_count: int = 0) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for current_type, args in _sample_values().items():
        legacy_type = _legacy_dataclass(current_type)
        current_factory = _factory(current_type, args)
        legacy_factory = _factory(legacy_type, args)
        results[current_type.__name__] = {
            "slotted": {
                **_layout_sizes(current_type, args),
                "measurements": [
                    _measure(current_factory, count, warmup_count) for count in counts
                ],
            },
            "unslotted_equivalent": {
                **_layout_sizes(legacy_type, args),
                "measurements": [
                    _measure(legacy_factory, count, warmup_count) for count in counts
                ],
            },
        }
    return results


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
    report = {
        "python": __import__("sys").version,
        "implementation": __import__("platform").python_implementation(),
        "counts": counts,
        "warmup_count": args.warmup_count,
        "results": run(counts=counts, warmup_count=args.warmup_count),
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
