"""Render a pytest resource-report JSON file as a CI-friendly Markdown summary."""

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


def _format_percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.1f}%"


def render(report: dict[str, Any]) -> str:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(  # noqa: TRY004 - preserve the CLI's validation contract
            "resource report does not contain a summary object"
        )
    implementation = report.get("implementation", "unknown")
    host_environment = report.get("host_environment", "unknown")
    return "\n".join(
        (
            "### Resource Report",
            "",
            f"Runtime: `{implementation}` on `{host_environment}`",
            "",
            "| Tests | Avg wall time | Avg CPU time | Avg CPU utilization | Avg RSS after | Avg RSS delta |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
            (
                "| "
                f"{summary.get('test_count', 'n/a')} | "
                f"{_format_ms(summary.get('average_wall_seconds'))} | "
                f"{_format_ms(summary.get('average_cpu_seconds'))} | "
                f"{_format_percent(summary.get('average_cpu_percent'))} | "
                f"{_format_mib(summary.get('average_rss_after_bytes'))} | "
                f"{_format_kib(summary.get('average_rss_delta_bytes'))} |"
            ),
            "",
            "CPU utilization is process CPU time divided by wall time and may exceed 100% on multicore hosts.",
            "",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        rendered = render(report)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"resource report summary failed: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
