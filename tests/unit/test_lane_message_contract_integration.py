from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from kogwistar.runtime import MappingStepResolver
from kogwistar.runtime.models import RunSuccess
from kogwistar.runtime.runtime import WorkflowRuntime
from kogwistar.server.auth_middleware import claims_ctx
from kogwistar.server.chat_service import ChatRunService
from kogwistar.server.run_registry import RunRegistry
from kogwistar_llm_wiki.ingest_pipeline import (
    IngestPipeline,
    IngestPipelineRequest,
    build_in_memory_namespace_engines,
    build_persistent_namespace_engines,
    build_postgres_namespace_engines,
)
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker


@dataclass
class _RuntimeLaneNode:
    id: str
    op: str
    metadata: dict
    terminal: bool = True

    def safe_get_id(self) -> str:
        return self.id


def _patch_single_step_design(monkeypatch, *, workflow_id: str, op: str) -> None:
    node = _RuntimeLaneNode(
        id=f"wf|{workflow_id}|send",
        op=op,
        metadata={"wf_terminal": True, "wf_start": True},
    )

    def _validate(*, workflow_engine, workflow_id, predicate_registry, resolver):
        return node, {node.id: node}, {node.id: []}

    monkeypatch.setattr("kogwistar.runtime.runtime.validate_workflow_design", _validate)


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


def test_repeated_sync_ingest_reuses_maintenance_request_message(tmp_path: Path):
    engines = build_in_memory_namespace_engines(tmp_path / "lane-idempotent-ingest")
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)

    first = pipeline.run(_request())
    second = pipeline.run(_request())

    assert second.maintenance_job_id == first.maintenance_job_id
    maintenance_rows = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:worker:maintenance"
    )
    assert len(maintenance_rows) == 1
    jobs = engines.conversation.meta_sqlite.list_index_jobs(
        namespace=WorkspaceNamespaces("demo").maintenance_jobs,
        limit=10,
    )
    assert len(jobs) == 1


def test_maintenance_lane_progress_reports_projected_request_and_reply(tmp_path: Path):
    engines = build_in_memory_namespace_engines(tmp_path / "lane-progress")
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)
    artifacts = pipeline.run(_request())

    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs("demo")

    ns = WorkspaceNamespaces("demo")
    service = ChatRunService(
        get_knowledge_engine=lambda: engines.kg,
        get_conversation_engine=lambda: engines.conversation,
        get_workflow_engine=lambda: engines.workflow,
        run_registry=RunRegistry(engines.conversation.meta_sqlite),
    )
    token = claims_ctx.set(
        {
            "storage_ns": ns.conv_bg,
            "capabilities": ["project_view", "workflow.run.read"],
        }
    )
    try:
        progress = service.lane_message_progress(
            conversation_id=f"maintenance:{artifacts.source_document_id}"
        )
    finally:
        claims_ctx.reset(token)

    by_type = {item["msg_type"]: item for item in progress["items"]}
    assert by_type["request.maintenance"]["status"] == "completed"
    assert by_type["request.maintenance"]["event_type"] == "worker.completed"
    assert by_type["reply.maintenance.completed"]["status"] == "pending"
    assert by_type["reply.maintenance.completed"]["inbox_id"] == "inbox:foreground"


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


def test_worker_recovers_after_lane_message_projection_repair(tmp_path: Path, monkeypatch):
    engines = build_persistent_namespace_engines(tmp_path / "sqlite-repair")
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)
    pipeline.run(_request())

    namespace = str(getattr(engines.conversation, "namespace", "default") or "default")
    assert engines.conversation.meta_sqlite.clear_projected_lane_messages(namespace) == 1
    assert (
        engines.conversation.list_projected_lane_messages(
            inbox_id="inbox:worker:maintenance"
        )
        == []
    )

    repair = engines.conversation.repair_lane_message_projection(namespace=namespace)
    assert repair.repaired_count == 1
    repaired_rows = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:worker:maintenance"
    )
    assert len(repaired_rows) == 1
    assert repaired_rows[0].status == "pending"

    worker = MaintenanceWorker(engines)
    monkeypatch.setattr(
        worker.runtime,
        "run",
        lambda **kwargs: type("RunResult", (), {"status": "finished"})(),
    )
    worker.process_pending_jobs("demo")

    maintenance_after = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:worker:maintenance"
    )
    foreground_rows = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:foreground"
    )
    assert maintenance_after[0].status == "completed"
    assert len(foreground_rows) == 1
    assert foreground_rows[0].msg_type == "reply.maintenance.completed"


def test_app_runtime_context_sends_projected_lane_message(
    tmp_path: Path, monkeypatch
):
    engines = build_in_memory_namespace_engines(tmp_path / "runtime-lane-message")
    workspace_id = "demo"
    ns = WorkspaceNamespaces(workspace_id)
    workflow_id = "wf-app-runtime-lane"
    _patch_single_step_design(monkeypatch, workflow_id=workflow_id, op="send_lane")
    resolver = MappingStepResolver()

    @resolver.register("send_lane")
    def _send(ctx):
        sent = ctx.send_lane_message(
            conversation_id="conv-app-runtime",
            inbox_id="inbox:worker:runtime",
            sender_id="lane:foreground",
            recipient_id="lane:worker:runtime",
            msg_type="request.runtime",
            payload={"workspace_id": workspace_id, "source": "runtime-context"},
            run_id=ctx.run_id,
            step_id=str(ctx.step_seq),
            correlation_id="corr:app-runtime",
        )
        return RunSuccess(
            conversation_node_id=None,
            state_update=[("u", {"lane_message_id": sent.message_id})],
        )

    def _send_lane_message_in_workspace(**kwargs):
        token = claims_ctx.set({"storage_ns": ns.conv_bg})
        try:
            return engines.conversation.send_lane_message(**kwargs)
        finally:
            claims_ctx.reset(token)

    def _list_lane_messages_in_workspace():
        token = claims_ctx.set({"storage_ns": ns.conv_bg})
        try:
            return engines.conversation.list_projected_lane_messages(
                inbox_id="inbox:worker:runtime"
            )
        finally:
            claims_ctx.reset(token)

    def _read_lane_nodes_in_workspace():
        with _temporary_namespace(engines.conversation, ns.conv_bg):
            return engines.conversation.read.get_nodes(
                where={"artifact_kind": "lane_message", "msg_type": "request.runtime"}
            )

    runtime = WorkflowRuntime(
        workflow_engine=engines.workflow,
        conversation_engine=engines.conversation,
        step_resolver=resolver,
        predicate_registry={},
        trace=False,
        lane_message_sender=_send_lane_message_in_workspace,
    )
    out = runtime.run(
        workflow_id=workflow_id,
        conversation_id="conv-app-runtime",
        turn_node_id="turn-app-runtime",
        initial_state={},
        run_id="run-app-runtime",
    )
    rows = _list_lane_messages_in_workspace()
    lane_nodes = _read_lane_nodes_in_workspace()

    assert out.status == "succeeded"
    assert len(rows) == 1
    assert [str(node.id) for node in lane_nodes] == [rows[0].message_id]
    assert rows[0].message_id == out.final_state["lane_message_id"]
    assert rows[0].namespace == ns.conv_bg
    assert rows[0].correlation_id == "corr:app-runtime"
    assert json.loads(rows[0].payload_json or "{}") == {
        "source": "runtime-context",
        "workspace_id": workspace_id,
    }
