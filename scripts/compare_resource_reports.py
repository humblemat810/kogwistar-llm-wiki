"""Compare pytest resource-report JSON files without hiding runtime boundaries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _format_ms(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 1000:.3f} ms"


def _format_mib(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) / (1024 * 1024):.2f} MiB"


def _format_kib(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) / 1024:.1f} KiB"


def _load_report(spec: str) -> tuple[str, dict[str, Any]]:
    try:
        label, raw_path = spec.split("=", 1)
    except ValueError as exc:
        raise ValueError("report must use LABEL=PATH") from exc
    if not label or not raw_path:
        raise ValueError("report label and path are required")
    path = Path(raw_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read resource report {path}: {exc}") from exc
    if not isinstance(report.get("summary"), dict):
        raise ValueError(  # noqa: TRY004 - preserve the CLI's validation contract
            f"resource report {path} has no summary object"
        )
    return label, report


def render(reports: list[tuple[str, dict[str, Any]]]) -> str:
    """Render a comparison table for reports from the same host environment."""

    if not reports:
        raise ValueError("at least one report is required")
    environments = {str(report.get("host_environment", "unknown")) for _, report in reports}
    if len(environments) != 1:
        raise ValueError(
            "resource reports use mixed host environments: "
            + ", ".join(sorted(environments))
        )
    environment = next(iter(environments))
    lines = [
        "### Resource Report Comparison",
        "",
        f"Host environment boundary: `{environment}`",
        "",
        "| Runtime | Tests | Avg wall time | Avg CPU time | Avg CPU utilization | Avg RSS after | Avg RSS delta |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, report in reports:
        summary = report["summary"]
        lines.append(
            "| "
            f"`{label}` | "
            f"{summary.get('test_count', 'n/a')} | "
            f"{_format_ms(summary.get('average_wall_seconds'))} | "
            f"{_format_ms(summary.get('average_cpu_seconds'))} | "
            f"{_format_percent(summary.get('average_cpu_percent'))} | "
            f"{_format_mib(summary.get('average_rss_after_bytes'))} | "
            f"{_format_kib(summary.get('average_rss_delta_bytes'))} |"
        )
    lines.extend(
        (
            "",
            "The comparison is diagnostic. RSS is process-wide, and CPU/JIT behavior plus runner load can vary.",
            "Use the individual JSON artifacts for per-test records and runtime metadata.",
            "",
        )
    )
    return "\n".join(lines)


def _format_percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="Resource report JSON and a display label; repeat for each runtime.",
    )
    args = parser.parse_args()
    try:
        reports = [_load_report(spec) for spec in args.report]
        print(render(reports), end="")
    except ValueError as exc:
        print(f"resource report comparison failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
