from __future__ import annotations

import argparse

from scripts.benchmark_conversation_two_stage import _node, run_conversation_materialization_benchmark
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.__main__ import _conversation_persistence_kwargs


def test_cli_forwards_conversation_two_stage_only_when_explicitly_enabled() -> None:
    assert _conversation_persistence_kwargs(
        argparse.Namespace(conversation_persistence_mode="single_stage")
    ) == {}
    assert _conversation_persistence_kwargs(
        argparse.Namespace(conversation_persistence_mode="two_stage")
    ) == {"conversation_persistence_mode": "two_stage"}


def test_conversation_two_stage_is_opt_in_and_keeps_knowledge_single_stage(tmp_path) -> None:
    engines = build_in_memory_namespace_engines(
        tmp_path,
        conversation_persistence_mode="two_stage",
    )
    try:
        assert engines.conversation.persistence_mode == "two_stage"
        assert engines.workflow.persistence_mode == "single_stage"
        assert engines.kg.persistence_mode == "single_stage"
        assert engines.wisdom.persistence_mode == "single_stage"
    finally:
        engines.close()


def test_two_stage_conversation_is_readable_before_batch_promotion(tmp_path) -> None:
    engines = build_in_memory_namespace_engines(
        tmp_path,
        conversation_persistence_mode="two_stage",
    )
    try:
        node = _node(1)
        engines.conversation.write.add_node(node)

        pending = engines.conversation.backend.node_get(
            ids=[node.safe_get_id()],
            include=["documents", "embeddings"],
        )
        assert pending["ids"] == [node.safe_get_id()]
        assert pending["embeddings"] == [None]
        assert engines.conversation.backend.node_query(
            query_embeddings=[[1.0, 1.0]],
            n_results=10,
        )["ids"] == [[]]

        metrics = engines.conversation.indexing.make_index_job_worker(
            batch_size=8,
            max_inflight=1,
            max_jobs_per_tick=20,
        ).tick()
        assert metrics.done >= 1
        assert engines.conversation.backend.node_get(
            ids=[node.safe_get_id()], include=["embeddings"]
        )["embeddings"][0] is not None
    finally:
        engines.close()


def test_mocked_conversation_two_stage_benchmark_reports_deferred_batch_speedup(tmp_path) -> None:
    report = run_conversation_materialization_benchmark(
        entity_count=8,
        provider_delay_ms=5.0,
        base_dir=tmp_path,
    )

    # The complete graph write also maintains existing join projections. The
    # comparison deliberately captures that real overhead rather than masking
    # it; deferred semantic promotion must still perform fewer provider calls.
    assert report.single_stage_provider_calls > 8
    assert report.two_stage_provider_calls_before_drain < report.single_stage_provider_calls
    assert report.two_stage_provider_calls_total < report.single_stage_provider_calls
    assert report.promoted_entities == 8
    assert report.two_stage_admission_ms < report.single_stage_admission_ms
    assert report.admission_speedup > 1.0
