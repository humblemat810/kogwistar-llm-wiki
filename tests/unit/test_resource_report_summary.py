import pytest

from scripts.summarize_resource_report import render


@pytest.mark.ci
def test_resource_report_summary_renders_comparable_runtime_metrics() -> None:
    report = {
        "implementation": "CPython",
        "host_environment": "windows",
        "summary": {
            "test_count": 3,
            "average_wall_seconds": 1.25,
            "average_cpu_seconds": 0.75,
            "average_cpu_percent": 60.0,
            "average_rss_after_bytes": 2 * 1024 * 1024,
            "average_rss_delta_bytes": 4096,
        },
    }

    rendered = render(report)

    assert "CPython" in rendered
    assert "windows" in rendered
    assert "1250.000 ms" in rendered
    assert "750.000 ms" in rendered
    assert "60.0%" in rendered
    assert "2.00 MiB" in rendered
    assert "4.0 KiB" in rendered


@pytest.mark.ci
def test_resource_report_summary_requires_a_summary_object() -> None:
    with pytest.raises(ValueError, match="summary object"):
        render({})
