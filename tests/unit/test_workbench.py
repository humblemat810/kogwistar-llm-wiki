from __future__ import annotations

from kogwistar_llm_wiki import (
    InvestigationHistoryService,
    KnowledgeWorkbench,
    SemanticLensRequest,
    SemanticLensService,
    build_in_memory_namespace_engines,
)


def test_deterministic_workbench_asks_and_records_grounded_turn():
    engines = build_in_memory_namespace_engines()
    try:
        now = iter([100, 100, 100, 100])
        workbench = KnowledgeWorkbench(
            lens_service=SemanticLensService(engines, clock_ms=lambda: next(now, 100)),
            history_service=InvestigationHistoryService(engines),
            clock_ms=lambda: 200,
        )
        turn = workbench.ask(
            request=SemanticLensRequest(workspace_id="workbench-test", query_text="not found"),
            session_id="session-1",
            mode="deterministic",
        )
        assert turn.answer.outcome == "no_change"
        assert turn.history.lens_id == turn.snapshot.lens_id
        assert turn.answer.cited_entity_ids == ()
    finally:
        engines.close()


def test_codex_mode_and_confirmation_delegate_without_direct_graph_write():
    engines = build_in_memory_namespace_engines()
    try:
        workbench = KnowledgeWorkbench(
            lens_service=SemanticLensService(engines, clock_ms=lambda: 300),
            history_service=InvestigationHistoryService(engines),
            clock_ms=lambda: 301,
        )
        turn = workbench.ask(
            request=SemanticLensRequest(workspace_id="workbench-test", query_text="not found"),
            session_id="session-2",
            mode="codex",
            agent_answer=lambda _snapshot: "agent answer",
        )
        proposal = {
            "lens_id": turn.snapshot.lens_id,
            "source_watermark": turn.snapshot.source_watermark,
            "operation": "no_change",
        }
        assert workbench.confirm_and_execute(
            snapshot=turn.snapshot,
            proposal=proposal,
            confirmed=False,
            execute=lambda _proposal: {"status": "should not run"},
        )["status"] == "confirmation_required"
        executed = workbench.confirm_and_execute(
            snapshot=turn.snapshot,
            proposal=proposal,
            confirmed=True,
            execute=lambda _proposal: {"status": "accepted_by_existing_command"},
        )
        assert executed["status"] == "accepted_by_existing_command"
    finally:
        engines.close()
