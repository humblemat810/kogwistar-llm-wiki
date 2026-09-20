"""Opt-in per-test wall, CPU, and memory measurements for pytest.

The reporter is intentionally disabled by default. Enable it with
``--resource-report`` or ``KOGWISTAR_RESOURCE_REPORT=1``. RSS is a best-effort
process-wide signal; traced allocations are more allocation-specific but slower
and unavailable on some PyPy builds.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

import pytest

try:
    import tracemalloc
except ImportError:  # PyPy builds may omit the optional CPython tracer.
    tracemalloc = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ResourceRecord:
    nodeid: str
    outcome: str
    wall_seconds: float
    cpu_seconds: float
    traced_peak_bytes: int | None
    traced_current_bytes: int | None
    rss_before_bytes: int | None
    rss_after_bytes: int | None
    rss_delta_bytes: int | None
    cpu_percent: float | None = None


def summarize_records(records: list[ResourceRecord]) -> dict[str, int | float | None]:
    """Return stable aggregate values suitable for CI comparisons."""

    def average(values: list[float]) -> float | None:
        return fmean(values) if values else None

    def cpu_percent(record: ResourceRecord) -> float:
        if record.cpu_percent is not None:
            return record.cpu_percent
        if record.wall_seconds <= 0:
            return 0.0
        return record.cpu_seconds / record.wall_seconds * 100.0

    traced_peaks = [
        float(record.traced_peak_bytes)
        for record in records
        if record.traced_peak_bytes is not None
    ]
    rss_deltas = [
        float(record.rss_delta_bytes)
        for record in records
        if record.rss_delta_bytes is not None
    ]
    rss_before = [
        float(record.rss_before_bytes)
        for record in records
        if record.rss_before_bytes is not None
    ]
    rss_after = [
        float(record.rss_after_bytes)
        for record in records
        if record.rss_after_bytes is not None
    ]
    return {
        "test_count": len(records),
        "average_wall_seconds": average([record.wall_seconds for record in records]),
        "average_cpu_seconds": average([record.cpu_seconds for record in records]),
        "average_cpu_percent": average([cpu_percent(record) for record in records]),
        "average_traced_peak_bytes": average(traced_peaks),
        "average_rss_before_bytes": average(rss_before),
        "average_rss_after_bytes": average(rss_after),
        "average_rss_delta_bytes": average(rss_deltas),
        "failed_count": sum(record.outcome == "failed" for record in records),
        "skipped_count": sum(record.outcome == "skipped" for record in records),
    }


@dataclass
class _State:
    records: list[ResourceRecord]
    outcomes: dict[str, dict[str, str]]
    started_tracemalloc: bool
    measure_tracemalloc: bool


_ACTIVE_STATE: _State | None = None


def _env_enabled() -> bool:
    return os.getenv("KOGWISTAR_RESOURCE_REPORT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _tracemalloc_requested(config: pytest.Config) -> bool:
    return bool(
        config.getoption("resource_report_tracemalloc", default=False)
        or os.getenv("KOGWISTAR_RESOURCE_REPORT_TRACEMALLOC", "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )


def _enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("resource_report", default=False) or _env_enabled())


def _rss_bytes() -> int | None:
    """Read current RSS without adding a runtime dependency."""

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

    if platform.system() == "Linux":
        try:
            with Path("/proc/self/statm").open(encoding="ascii") as stream:
                resident_pages = int(stream.read().split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, IndexError, ValueError):
            return None

    # macOS and other Unix platforms expose only the process high-water mark
    # through the standard library.  Keep it labelled as best effort.
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value * (1024 if platform.system() == "Linux" else 1))


def _host_environment() -> str:
    """Return the OS boundary required for fair runtime comparisons."""
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


def _outcome(outcomes: dict[str, str]) -> str:
    if "failed" in outcomes.values():
        return "failed"
    if "skipped" in outcomes.values():
        return "skipped"
    return "passed"


def _state(config: pytest.Config) -> _State | None:
    return getattr(config, "_kogwistar_resource_report_state", None)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("resource reporting")
    group.addoption(
        "--resource-report",
        action="store_true",
        default=False,
        help="Measure per-test wall time, CPU time, and best-effort memory usage.",
    )
    group.addoption(
        "--resource-report-json",
        metavar="PATH",
        default=None,
        help="Write the per-test resource report and averages to PATH.",
    )
    group.addoption(
        "--resource-report-tracemalloc",
        action="store_true",
        default=False,
        help="Also measure traced allocations; slower, and unavailable on some PyPy builds.",
    )


def pytest_configure(config: pytest.Config) -> None:
    global _ACTIVE_STATE
    if not _enabled(config):
        _ACTIVE_STATE = None
        return
    measure_tracemalloc = _tracemalloc_requested(config) and tracemalloc is not None
    started_tracemalloc = measure_tracemalloc and not tracemalloc.is_tracing()
    if started_tracemalloc and tracemalloc is not None:
        tracemalloc.start()
    state = _State([], {}, started_tracemalloc, measure_tracemalloc)
    config._kogwistar_resource_report_state = state
    _ACTIVE_STATE = state


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    state = _ACTIVE_STATE
    if state is None:
        return
    state.outcomes.setdefault(report.nodeid, {})[report.when] = report.outcome


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Any:
    config = item.config
    state = _state(config)
    if state is None:
        yield
        return
    if state.measure_tracemalloc and tracemalloc is not None and tracemalloc.is_tracing():
        tracemalloc.reset_peak()
    rss_before = _rss_bytes()
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    yield
    current_bytes: int | None = None
    peak_bytes: int | None = None
    if state.measure_tracemalloc and tracemalloc is not None and tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        current_bytes = current
        peak_bytes = peak
    rss_after = _rss_bytes()
    wall_seconds = time.perf_counter() - started_wall
    cpu_seconds = time.process_time() - started_cpu
    state.records.append(
        ResourceRecord(
            nodeid=item.nodeid,
            outcome=_outcome(state.outcomes.get(item.nodeid, {})),
            wall_seconds=wall_seconds,
            cpu_seconds=cpu_seconds,
            traced_peak_bytes=peak_bytes,
            traced_current_bytes=current_bytes,
            rss_before_bytes=rss_before,
            rss_after_bytes=rss_after,
            rss_delta_bytes=(rss_after - rss_before)
            if rss_before is not None and rss_after is not None
            else None,
            cpu_percent=cpu_seconds / max(wall_seconds, 1e-12) * 100.0,
        )
    )


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    state = _state(config)
    if state is None:
        return
    summary = summarize_records(state.records)
    terminalreporter.write_sep(
        "=",
        "resource report",
    )
    terminalreporter.write_line(
        "tests={test_count} average_wall_ms={wall} average_cpu_ms={cpu} "
        "average_cpu_percent={cpu_percent} "
        "average_rss_after_mib={rss_after} average_rss_delta_kib={rss_delta} "
        "average_traced_peak_kib={memory}".format(
            test_count=summary["test_count"],
            wall=_milliseconds(summary["average_wall_seconds"]),
            cpu=_milliseconds(summary["average_cpu_seconds"]),
            cpu_percent=_percentage(summary["average_cpu_percent"]),
            rss_after=_mebibytes(summary["average_rss_after_bytes"]),
            rss_delta=_kibibytes(summary["average_rss_delta_bytes"]),
            memory=_kibibytes(summary["average_traced_peak_bytes"]),
        )
    )


def _milliseconds(value: object) -> str:
    return "n/a" if value is None else f"{float(value) * 1000:.3f}"


def _kibibytes(value: object) -> str:
    return "n/a" if value is None else f"{float(value) / 1024:.1f}"


def _mebibytes(value: object) -> str:
    return "n/a" if value is None else f"{float(value) / (1024 * 1024):.2f}"


def _percentage(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.1f}%"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _ACTIVE_STATE
    state = _state(session.config)
    if state is None:
        return
    summary = summarize_records(state.records)
    output = session.config.getoption("resource_report_json", default=None)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "python_executable": str(Path(sys.executable).resolve()),
            "host_environment": _host_environment(),
            "host_system": platform.system(),
            "host_release": platform.release(),
            "host_machine": platform.machine(),
            "tracemalloc_enabled": state.measure_tracemalloc,
            "exitstatus": exitstatus,
            "summary": summary,
            "tests": [asdict(record) for record in state.records],
        }
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if (
        state.started_tracemalloc
        and tracemalloc is not None
        and tracemalloc.is_tracing()
    ):
        tracemalloc.stop()
    _ACTIVE_STATE = None
