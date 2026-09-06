from __future__ import annotations

from kogwistar_llm_wiki import (
    InvestigationHistoryService,
    SemanticLensRequest,
    SemanticLensService,
    build_in_memory_namespace_engines,
)


def test_investigation_history_is_durable_and_queryable():
    engines = build_in_memory_namespace_engines()
    try:
        workspace_id = "history-test"
        lens = SemanticLensService(engines, clock_ms=lambda: 10).resolve(
            SemanticLensRequest(workspace_id=workspace_id, query_text="missing", source_watermark=3)
        )
        lens_service = SemanticLensService(engines, clock_ms=lambda: 10)
        outcome = lens_service.investigate(
            session_id="session-1",
            snapshot=lens,
            insufficiency_reason="No grounded match.",
        )
        history = InvestigationHistoryService(engines)
        written = history.record(
            workspace_id=workspace_id,
            session_id="session-1",
            question="What is missing?",
            action_kind="query",
            snapshot=lens,
            outcome=outcome,
            created_at_ms=20,
        )

        records = history.query(workspace_id=workspace_id, session_id="session-1")
        assert [record.id for record in records] == [written.id]
        assert records[0].lens_id == lens.lens_id
        assert records[0].source_watermark == 3
        assert records[0].outcome == "no_change"
    finally:
        engines.close()
