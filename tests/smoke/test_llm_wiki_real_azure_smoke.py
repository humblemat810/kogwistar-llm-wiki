from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from kogwistar_llm_wiki import IngestPipeline
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.models import IngestPipelineRequest
from kogwistar_llm_wiki.provider_config import resolve_parser_provider_settings


def _build_mid_sized_markdown(title: str) -> str:
    sections: list[str] = [f"# {title}", ""]
    for index in range(1, 6):
        sections.extend(
            [
                f"## Section {index}",
                "",
                (
                    "This section explains the integration contract, the parser boundary, "
                    "the retry shape, and the runtime reporting path in a way that should "
                    "be meaningful to a real model."
                ),
                "",
                (
                    "The maintenance lane records append-only graph changes and tombstones "
                    "instead of in-place mutation, which is important for provenance and "
                    "later review."
                ),
                "",
                f"- Item {index}.1",
                f"- Item {index}.2",
                "",
            ]
        )
    sections.extend(
        [
            "## Closing Notes",
            "",
            "The document intentionally mixes narrative text, headings, and list items so the parser has enough structure to work with during a real smoke run.",
        ]
    )
    return "\n".join(sections)


def _resolve_real_azure_settings(model: str):
    settings = resolve_parser_provider_settings(provider="azure_openai", model=model)
    api_key_env = settings.parser.api_key_env
    if not api_key_env or not os.getenv(api_key_env):
        pytest.skip(f"missing Azure OpenAI API key env for model {model!r}: {api_key_env!r}")
    if not settings.parser.base_url:
        pytest.skip(f"missing Azure OpenAI endpoint for model {model!r}")
    if not settings.parser.api_version:
        pytest.skip(f"missing Azure OpenAI api version for model {model!r}")
    return settings


@pytest.mark.manual
@pytest.mark.slow
@pytest.mark.parametrize(
    "model",
    [
        pytest.param("gpt-5-chat", id="gpt5-chat"),
        pytest.param("gpt-5-mini", id="gpt5-mini"),
        pytest.param("gpt-5-nano", id="gpt5-nano"),
    ],
)
def test_llm_wiki_real_azure_workflow_smoke(tmp_path: Path, model: str) -> None:
    if os.getenv("KOGWISTAR_LLM_WIKI_REAL_SMOKE") != "1":
        pytest.skip("set KOGWISTAR_LLM_WIKI_REAL_SMOKE=1 to run the real Azure smoke test")
    pytest.importorskip("langchain_openai")
    _resolve_real_azure_settings(model)
    debug_dir = tmp_path / f"debug-{model}"
    engines = build_in_memory_namespace_engines()
    pipeline = IngestPipeline(engines, debug_run_dir=debug_dir)

    request = IngestPipelineRequest(
        workspace_id=f"manual-{model}",
        source_uri=f"file:///manual/{model}.md",
        title=f"Manual Azure Smoke {model}",
        raw_text=_build_mid_sized_markdown(f"Manual Azure Smoke {model}"),
        source_format="markdown",
        operation_mode="parse_first",
        parser_mode="azure_openai",
        parser_lane="workflow_layered",
        promotion_mode="pending",
        llm_provider="azure_openai",
        llm_model=model,
    )

    artifacts = pipeline.run(request)

    log_path = debug_dir / "llm_wiki.log"
    trace_path = debug_dir / "run_trace.jsonl"
    stats_path = debug_dir / "llm_wiki_stats.sqlite3"

    assert artifacts.source_document_id
    assert artifacts.maintenance_job_id
    assert log_path.exists()
    assert trace_path.exists()
    assert stats_path.exists()

    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(event["stage"] == "ingest_run_start" for event in trace_events)
    assert any(event["stage"] == "parse_statistics_recorded" for event in trace_events)

    with sqlite3.connect(stats_path) as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM parse_run_statistics").fetchone()[0]
        row = conn.execute(
            """
            SELECT workspace_id, parser_lane, parser_mode, proposal_mode, provider, model, document_length_chars
            FROM parse_run_statistics
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row_count >= 1
    assert row[0] == f"manual-{model}"
    assert row[1] == "workflow_layered"
    assert row[2] == "azure_openai"
    assert row[4] == "azure"
    assert row[5] == model
    assert row[6] >= len(request.raw_text)
