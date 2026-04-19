from __future__ import annotations

import os
from pathlib import Path

import pytest

from kogwistar_llm_wiki.ingest_pipeline import (
    IngestPipeline,
    IngestPipelineRequest,
    build_in_memory_namespace_engines,
    build_persistent_namespace_engines,
    build_postgres_namespace_engines,
)
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.worker import MaintenanceWorker


def _request() -> IngestPipelineRequest:
    return IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days. Either party may terminate with notice.",
        promotion_mode="sync",
    )


def _lane_projection_snapshot(engine, *, inbox_id: str) -> list[tuple[str, str, str, int, int]]:
    rows = engine.list_projected_lane_messages(inbox_id=inbox_id)
    return [
        (row.message_id, row.msg_type, row.status, int(row.seq), int(row.retry_count))
        for row in rows
    ]


def _build_namespace_engines(backend_name: str, tmp_path: Path):
    if backend_name == "memory":
        return build_in_memory_namespace_engines(tmp_path / "memory")
    if backend_name == "sqlite":
        return build_persistent_namespace_engines(tmp_path / "sqlite")
    if backend_name == "postgres":
        dsn = os.getenv("KOGWISTAR_LLM_WIKI_TEST_PG_DSN")
        if not dsn:
            pytest.skip("KOGWISTAR_LLM_WIKI_TEST_PG_DSN not set")
        pytest.importorskip("pgvector")
        return build_postgres_namespace_engines(
            base_dir=tmp_path / "postgres",
            dsn=dsn,
        )
    raise ValueError(f"Unsupported backend_name={backend_name!r}")


@pytest.mark.parametrize("backend_name", ["memory", "sqlite", "postgres"])
def test_lane_message_request_reply_round_trip_is_backend_agnostic(tmp_path: Path, backend_name: str):
    engines = _build_namespace_engines(backend_name, tmp_path)
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)

    artifacts = pipeline.run(_request())

    maintenance_rows = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:worker:maintenance"
    )
    assert len(maintenance_rows) == 1
    request_row = maintenance_rows[0]
    assert request_row.msg_type == "request.maintenance"
    assert request_row.status == "pending"

    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs("demo")

    maintenance_after = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:worker:maintenance"
    )
    assert len(maintenance_after) == 1
    assert maintenance_after[0].message_id == request_row.message_id
    assert maintenance_after[0].status == "completed"

    foreground_rows = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:foreground"
    )
    assert len(foreground_rows) == 1
    assert foreground_rows[0].msg_type == "reply.maintenance.completed"
    assert foreground_rows[0].correlation_id == request_row.message_id

    assert artifacts.maintenance_job_id


@pytest.mark.parametrize("backend_name", ["memory", "sqlite", "postgres"])
def test_lane_message_projection_claim_ack_requeue_contract_is_backend_agnostic(
    tmp_path: Path,
    backend_name: str,
):
    engines = _build_namespace_engines(backend_name, tmp_path)
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)
    pipeline.run(_request())

    before = _lane_projection_snapshot(
        engines.conversation,
        inbox_id="inbox:worker:maintenance",
    )
    assert len(before) == 1
    assert before[0][1] == "request.maintenance"
    assert before[0][2] == "pending"

    claimed = engines.conversation.claim_projected_lane_messages(
        inbox_id="inbox:worker:maintenance",
        claimed_by="test-worker",
        limit=1,
        lease_seconds=30,
    )
    assert len(claimed) == 1
    assert claimed[0].status == "claimed"

    engines.conversation.requeue_projected_lane_message(
        message_id=claimed[0].message_id,
        claimed_by="test-worker",
        error={"reason": "retry"},
        delay_seconds=0,
    )
    engines.conversation.update_lane_message_status(
        message_id=claimed[0].message_id,
        status="pending",
        error={"reason": "retry"},
    )

    after_requeue = _lane_projection_snapshot(
        engines.conversation,
        inbox_id="inbox:worker:maintenance",
    )
    assert after_requeue[0][2] == "pending"
    assert after_requeue[0][4] == 1

    claimed_again = engines.conversation.claim_projected_lane_messages(
        inbox_id="inbox:worker:maintenance",
        claimed_by="test-worker",
        limit=1,
        lease_seconds=30,
    )
    assert len(claimed_again) == 1
    engines.conversation.ack_projected_lane_message(
        message_id=claimed_again[0].message_id,
        claimed_by="test-worker",
    )
    engines.conversation.update_lane_message_status(
        message_id=claimed_again[0].message_id,
        status="completed",
        completed=True,
    )

    after_ack = _lane_projection_snapshot(
        engines.conversation,
        inbox_id="inbox:worker:maintenance",
    )
    assert after_ack[0][2] == "completed"


def test_sqlite_lane_message_projection_persists_across_engine_reload(tmp_path: Path):
    base_dir = tmp_path / "sqlite-persist"
    pipeline = IngestPipeline(build_persistent_namespace_engines(base_dir))
    materialize_maintenance_designs(pipeline.engines.workflow)
    pipeline.run(_request())

    before = _lane_projection_snapshot(
        pipeline.engines.conversation,
        inbox_id="inbox:worker:maintenance",
    )
    reloaded = build_persistent_namespace_engines(base_dir)
    after = _lane_projection_snapshot(
        reloaded.conversation,
        inbox_id="inbox:worker:maintenance",
    )
    assert after == before
