from __future__ import annotations

import pytest

from tests._helpers.resource_report import (
    ResourceRecord,
    _host_environment,
    summarize_records,
)


@pytest.mark.ci
def test_resource_report_summarizes_per_test_wall_cpu_and_memory() -> None:
    records = [
        ResourceRecord("test_a", "passed", 0.010, 0.004, 1024, 512, 4096, 5120, 1024),
        ResourceRecord("test_b", "skipped", 0.020, 0.006, 2048, 768, 5120, 5120, 0),
    ]

    assert summarize_records(records) == {
        "test_count": 2,
        "average_wall_seconds": 0.015,
        "average_cpu_seconds": 0.005,
        "average_cpu_percent": 35.0,
        "average_traced_peak_bytes": 1536.0,
        "average_rss_before_bytes": 4608.0,
        "average_rss_after_bytes": 5120.0,
        "average_rss_delta_bytes": 512.0,
        "failed_count": 0,
        "skipped_count": 1,
    }


@pytest.mark.ci
def test_resource_report_has_an_explicit_host_comparison_boundary() -> None:
    assert _host_environment() in {"windows", "wsl", "linux", "darwin"}
