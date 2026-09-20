from __future__ import annotations

import pytest

from scripts.compare_resource_reports import render


def _report(environment: str = "linux") -> dict[str, object]:
    return {
        "implementation": "CPython",
        "host_environment": environment,
        "summary": {
            "test_count": 2,
            "average_wall_seconds": 0.1,
            "average_cpu_seconds": 0.05,
            "average_cpu_percent": 50.0,
            "average_rss_after_bytes": 2 * 1024 * 1024,
            "average_rss_delta_bytes": 1024,
        },
    }


@pytest.mark.ci
def test_comparison_renders_runtime_rows_and_aggregate_metrics() -> None:
    rendered = render([("cpython313", _report()), ("pypy311", _report())])

    assert "Host environment boundary: `linux`" in rendered
    assert "`cpython313`" in rendered
    assert "`pypy311`" in rendered
    assert "100.000 ms" in rendered
    assert "50.0%" in rendered
    assert "2.00 MiB" in rendered
    assert "1.0 KiB" in rendered


@pytest.mark.ci
def test_comparison_rejects_mixed_host_environments() -> None:
    with pytest.raises(ValueError, match="mixed host environments"):
        render([("windows", _report("windows")), ("wsl", _report("wsl"))])


@pytest.mark.ci
def test_comparison_requires_a_report() -> None:
    with pytest.raises(ValueError, match="at least one report"):
        render([])
