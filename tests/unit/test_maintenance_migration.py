from __future__ import annotations

from kogwistar_llm_wiki.maintenance_migration import compare_ingest_operation_modes


def test_compare_ingest_operation_modes_runs_same_document_shape_across_modes(pipeline, ingest_request) -> None:
    comparisons = compare_ingest_operation_modes(
        pipeline,
        ingest_request.model_copy(update={"workspace_id": "migration-compare"}),
    )

    by_mode = {item.operation_mode: item for item in comparisons}
    assert set(by_mode) == {"parse_first", "maintenance_first", "hybrid"}
    assert by_mode["parse_first"].artifacts.operation_mode == "parse_first"
    assert by_mode["maintenance_first"].artifacts.operation_mode == "maintenance_first"
    assert by_mode["hybrid"].artifacts.operation_mode == "hybrid"
    assert by_mode["parse_first"].graph_quality == "stable"
    assert by_mode["maintenance_first"].graph_quality == "seeded"
    assert by_mode["hybrid"].graph_quality == "expanding"
    for item in comparisons:
        assert item.initial_latency_ms >= 0
        assert item.accepted_patch_count >= 0
        assert item.fallback_rate >= 0.0
        assert item.total_model_cost >= 0.0
