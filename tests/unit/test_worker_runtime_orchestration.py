from __future__ import annotations

import json
import threading
import time

import pytest
from pathlib import Path
from types import SimpleNamespace
import kogwistar.engine_core.in_memory_meta as in_memory_meta
from kogwistar.runtime import BudgetAttribution, BudgetEvent
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline, IngestPipelineRequest
import kogwistar_llm_wiki.worker as worker_module
from kogwistar_llm_wiki.projection_worker import ProjectionWorker
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar.engine_core.jobs import DurableQueueUnavailableError
from kogwistar.engine_core.in_memory_meta import InMemoryMetaStore
from kogwistar.engine_core.jobs import JobQueueSubsystem
from kogwistar_llm_wiki.maintenance_strategies import MaintenanceJobExecutionContext
from kogwistar_llm_wiki.maintenance_patches import MaintenancePatch
from kogwistar_llm_wiki.maintenance_statistics import build_maintenance_statistics
from kogwistar_llm_wiki.longrun_trace_sink import LongRunJsonlTraceSink


def _job_field(job, name: str):
    if isinstance(job, dict):
        return job.get(name)
    return getattr(job, name, None)


def _job_payload(job) -> dict:
    payload = _job_field(job, "payload_json")
    if isinstance(payload, str) and payload:
        return json.loads(payload)
    return {}


def _lane_payload(node) -> dict:
    payload = node.metadata.get("payload_json")
    if isinstance(payload, str) and payload:
        return json.loads(payload)
    return {}


def test_fair_maintenance_worker_processes_one_claimed_job_per_poll() -> None:
    class FakeJobs:
        def __init__(self) -> None:
            self.claims = 0

        def require_available(self, *, claim: bool) -> None:
            assert claim is True

        def claim(self, *, limit: int, lease_seconds: int, namespace: str):
            self.claims += 1
            assert limit == 1
            return [SimpleNamespace(job_id="job-1")] if self.claims == 1 else [SimpleNamespace(job_id="job-2")]

    jobs = FakeJobs()
    worker = object.__new__(MaintenanceWorker)
    worker.engines = SimpleNamespace(conversation=SimpleNamespace(jobs=jobs))
    worker.fair_scheduling = True
    handled: list[str] = []
    worker._handle_job = lambda _workspace, job: handled.append(job.job_id)

    worker.process_pending_jobs("demo")

    assert handled == ["job-1"]
    assert jobs.claims == 1


def test_lease_renewal_exception_fences_the_worker_claim() -> None:
    events: list[dict[str, object]] = []

    class FailingJobs:
        def renew_lease(self, *_args, **_kwargs):
            raise RuntimeError("connection lost")

    worker = object.__new__(MaintenanceWorker)
    worker.worker_id = "maintenance-renewal-test"
    worker.engines = SimpleNamespace(conversation=SimpleNamespace(jobs=FailingJobs()))
    worker.lease_renew_interval_seconds = 0.001
    worker.lease_progress_grace_seconds = 90
    worker._last_progress_monotonic = time.monotonic()
    worker._claim_lost = threading.Event()
    worker.trace_sink = events.append

    stop = threading.Event()
    ctx = SimpleNamespace(job_id="job-renewal", job=SimpleNamespace(claim_token="claim-1"))
    lease_thread = threading.Thread(
        target=worker._renew_claim_while_progressing,
        args=(ctx, stop),
        name="renewal-test",
    )
    lease_thread.start()
    lease_thread.join(timeout=2)
    stop.set()
    lease_thread.join(timeout=2)

    assert not lease_thread.is_alive()
    assert worker._claim_lost.is_set()
    assert any(event["event"] == "maintenance_lease_renewal_failed" for event in events)


def test_claim_loss_before_graph_patch_never_calls_the_applier(monkeypatch) -> None:
    applied: list[object] = []
    traces: list[dict[str, object]] = []

    class FakeJobs:
        def accepted_candidate(self, _job):
            return None

        def accept_candidate(self, _job, _candidate):
            return {"status": "accepted"}

    worker = object.__new__(MaintenanceWorker)
    worker.engines = SimpleNamespace(conversation=SimpleNamespace(jobs=FakeJobs()))
    worker._claim_lost = threading.Event()
    worker._claim_lost.set()
    worker.trace_sink = traces.append
    worker._evaluate_maintenance_guard = lambda _ctx: SimpleNamespace(status="ready")
    worker._emit_lane_reply = lambda **_kwargs: applied.append("reply")
    worker._acknowledge_job = lambda _ctx: applied.append("ack")

    monkeypatch.setattr(
        worker_module,
        "apply_maintenance_patch_for_scope",
        lambda *_args, **_kwargs: applied.append("graph")
        or SimpleNamespace(status=SimpleNamespace(value="applied")),
    )
    patch = MaintenancePatch(
        patch_id="patch-lease-loss",
        intent="derive_summary",
        scope={"workspace_id": "workspace-lease-loss"},
        operations=[
            {
                "operation_id": "op-1",
                "kind": "ADD_NODE",
                "node_id": "node-1",
            }
        ],
    )
    ctx = SimpleNamespace(
        workspace_id="workspace-lease-loss",
        job=SimpleNamespace(claim_token="claim-1"),
        job_id="job-lease-loss",
        payload={"patch": patch.model_dump(mode="json")},
        request_node_id="request-lease-loss",
        lane_message_id="",
        maintenance_kind="graph_patch_apply",
    )

    worker._handle_graph_patch_apply_strategy(ctx)

    assert applied == []
    assert any(event["event"] == "maintenance_repeated_work_discarded" for event in traces)


def test_expired_maintenance_claim_cannot_be_completed_by_stale_worker() -> None:
    """A first persisted result may win, but a stale queue ack must not.

    This models the failure boundary for two maintenance workers sharing one
    durable queue: worker A persists a realistic maintenance result, its lease
    expires, worker B reclaims the job and observes that result, and A then
    finishes after losing ownership.  A's result remains valid, but only B may
    acknowledge B's claim.
    """
    clock = [100]
    original_now_epoch = in_memory_meta._now_epoch
    in_memory_meta._now_epoch = lambda: clock[0]
    try:
        store = InMemoryMetaStore()
        maintenance_namespace = WorkspaceNamespaces("workspace-race").maintenance_jobs
        store.enqueue_index_job(
            job_id="maintenance-race-1",
            namespace=maintenance_namespace,
            entity_kind="maintenance",
            entity_id="document-1",
            index_kind="distill",
            op="UPSERT",
            payload_json="{}",
            max_retries=5,
        )

        real_queue = JobQueueSubsystem(SimpleNamespace(meta_sqlite=store))
        result_persisted = threading.Event()
        worker_b_observed_result = threading.Event()
        worker_a_ack_attempted = threading.Event()
        result_store: dict[str, object] = {}
        after_worker_a_ack: list[object] = []
        completion_calls: list[tuple[str, str | None]] = []

        class SynchronizedJobs:
            """Real queue facade with deterministic completion ordering."""

            def require_available(self, *, claim: bool) -> None:
                real_queue.require_available(claim=claim)

            def claim(self, **kwargs):
                return real_queue.claim(**kwargs)

            def mark_done(self, job_id: str, *, claim_token: str | None = None) -> None:
                worker_name = threading.current_thread().name
                if worker_name == "maintenance-A":
                    real_queue.mark_done(job_id, claim_token=claim_token)
                    completion_calls.append((worker_name, claim_token))
                    after_worker_a_ack.append(
                        store.list_index_jobs(namespace=maintenance_namespace, limit=1)[0]
                    )
                    worker_a_ack_attempted.set()
                    return
                assert worker_name == "maintenance-B"
                assert worker_a_ack_attempted.wait(5)
                real_queue.mark_done(job_id, claim_token=claim_token)
                completion_calls.append((worker_name, claim_token))

            def retry_or_fail(self, *args, **kwargs):
                return real_queue.retry_or_fail(*args, **kwargs)

            def renew_lease(self, *args, **kwargs):
                return real_queue.renew_lease(*args, **kwargs)

        jobs = SynchronizedJobs()
        engines = SimpleNamespace(conversation=SimpleNamespace(jobs=jobs))
        payload = {
            "workspace_id": "workspace-race",
            "request_node_id": "maintenance-request-1",
            "lane_message_id": "lane-message-1",
            "source_document_id": "source-document-1",
            "source_revision_id": "revision-1",
            "source_digest": "sha256:source-document-1",
            "required_stage": "parsed_graph_persisted",
            "maintenance_kind": "document_parse_graph",
            "maintenance_phase_index": 0,
            "maintenance_round": 0,
        }

        def make_worker(name: str) -> MaintenanceWorker:
            worker = object.__new__(MaintenanceWorker)
            worker.engines = engines
            worker.worker_id = name
            worker.fair_scheduling = True
            worker.lease_seconds = 10
            worker._claim_lost = threading.Event()
            worker._last_progress_monotonic = 0.0
            worker.trace_sink = None
            worker._evaluate_maintenance_guard = lambda _ctx: SimpleNamespace(status="ready")
            worker._advance_maintenance_plan = lambda _ctx: False

            def handle_job(_workspace_id: str, job) -> None:
                from kogwistar_llm_wiki.maintenance_strategies import MaintenanceJobExecutionContext

                ctx = MaintenanceJobExecutionContext(
                    workspace_id="workspace-race",
                    job=job,
                    job_id=job.job_id,
                    payload=dict(payload),
                    request_node=None,
                    request_node_id="maintenance-request-1",
                    lane_message_id="lane-message-1",
                    maintenance_kind="document_parse_graph",
                )
                worker._handle_document_parse_strategy(ctx)

            worker._handle_job = handle_job

            def document_parser(_ctx):
                result = {
                    "node_count": 3,
                    "edge_count": 2,
                    "llm_call_count": 1,
                    "result_id": "maintenance-result-first-write",
                }
                if name == "maintenance-A":
                    result_store.update(result)
                    result_persisted.set()
                    assert worker_b_observed_result.wait(5)
                else:
                    assert result_persisted.wait(5)
                    assert result_store["result_id"] == "maintenance-result-first-write"
                    worker_b_observed_result.set()
                return result

            worker.document_parser = document_parser
            return worker

        worker_a = make_worker("maintenance-A")
        worker_b = make_worker("maintenance-B")

        def run_worker(worker: MaintenanceWorker) -> None:
            worker.process_pending_jobs("workspace-race")

        thread_a = threading.Thread(target=run_worker, args=(worker_a,), name="maintenance-A")
        thread_a.start()
        assert result_persisted.wait(5)

        # Move time past A's lease, then let B claim the same durable job.
        clock[0] = 111
        thread_b = threading.Thread(target=run_worker, args=(worker_b,), name="maintenance-B")
        thread_b.start()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)
        assert not thread_a.is_alive()
        assert not thread_b.is_alive()

        assert result_store["result_id"] == "maintenance-result-first-write"
        assert after_worker_a_ack
        assert after_worker_a_ack[0].status == "DOING"
        assert completion_calls[0][0] == "maintenance-A"
        assert completion_calls[0][1] is not None
        assert completion_calls[1][0] == "maintenance-B"
        assert completion_calls[1][1] is not None
        current = store.list_index_jobs(namespace=maintenance_namespace, limit=1)[0]
        assert current.status == "DONE"
    finally:
        in_memory_meta._now_epoch = original_now_epoch


def test_duplicate_maintenance_attempt_is_traceable_and_costed_once_per_llm_call(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Duplicate execution preserves both costs and one user-visible reply.

    A's result is allowed to win before B reclaims the expired queue lease.
    Both fake runtime attempts still represent real provider calls, so each
    attempt contributes distinct usage events and the maintenance-job
    projection sums both costs. Worker traces and the conversation lane retain
    the attempt history while deterministic reply idempotency leaves one
    completed foreground reply.
    """
    request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    artifacts = pipeline.run(request)
    workspace_id = request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    maintenance_namespace = ns.maintenance_jobs

    clock = [100]
    original_now_epoch = in_memory_meta._now_epoch
    in_memory_meta._now_epoch = lambda: clock[0]
    try:
        queue = pipeline.engines.conversation.jobs
        worker_a_job = queue.claim(
            namespace=maintenance_namespace,
            limit=1,
            lease_seconds=10,
        )[0]
        assert worker_a_job.payload["source_document_id"] == artifacts.source_document_id
        assert worker_a_job.payload["lane_message_id"]

        clock[0] = 111
        worker_b_job = queue.claim(
            namespace=maintenance_namespace,
            limit=1,
            lease_seconds=10,
        )[0]
        assert worker_a_job.claim_token != worker_b_job.claim_token

        first_result: dict[str, object] = {
            "result_id": "maintenance-result-first-write",
            "node_count": 3,
            "edge_count": 2,
        }

        class FakeRuntime:
            def __init__(self, run_id: str, *, expect_first_result: bool = False) -> None:
                self.run_id = run_id
                self.expect_first_result = expect_first_result

            def run(self, **kwargs):
                if self.expect_first_result:
                    assert first_result["result_id"] == "maintenance-result-first-write"
                ledger = kwargs["initial_state"]["_deps"]["budget_ledger"]
                attribution = BudgetAttribution(
                    source_document_id=artifacts.source_document_id,
                    operation_id=f"provider-call-{self.run_id}",
                    operation_kind="llm_call",
                    provider="fake",
                    model="fake-maintenance-model",
                )
                ledger.ingest(
                    BudgetEvent(
                        event_id=f"{self.run_id}:tokens",
                        run_id=self.run_id,
                        source="fake-provider",
                        kind="token",
                        amount=100,
                        unit="input_tokens",
                        attribution=attribution,
                    )
                )
                ledger.ingest(
                    BudgetEvent(
                        event_id=f"{self.run_id}:cost",
                        run_id=self.run_id,
                        source="fake-provider",
                        kind="cost",
                        amount=0.05,
                        unit="total_cost",
                        attribution=attribution,
                    )
                )
                return SimpleNamespace(status="finished", run_id=self.run_id)

        def run_attempt(
            job,
            runtime: FakeRuntime,
            trace: list[dict[str, object]],
        ) -> MaintenanceWorker:
            worker = MaintenanceWorker(
                pipeline.engines,
                trace_sink=lambda row: record_trace(trace, row),
            )
            worker.runtime = runtime
            monkeypatch.setattr(
                worker,
                "_evaluate_maintenance_guard",
                lambda _ctx: SimpleNamespace(status="ready"),
            )
            monkeypatch.setattr(
                pipeline.engines.workflow.read,
                "node_exists",
                lambda **_kwargs: True,
            )
            payload = dict(job.payload)
            payload["maintenance_kind"] = "distill"
            context = MaintenanceJobExecutionContext(
                workspace_id=workspace_id,
                job=job,
                job_id=job.job_id,
                payload=payload,
                request_node=None,
                request_node_id=str(payload["request_node_id"]),
                lane_message_id=str(payload["lane_message_id"]),
                maintenance_kind="distill",
            )
            worker._handle_runtime_workflow_strategy(context)
            return worker

        trace_a: list[dict[str, object]] = []
        trace_b: list[dict[str, object]] = []

        trace_sink = LongRunJsonlTraceSink(
            jsonl_path=tmp_path / "maintenance_worker_trace.jsonl",
        )

        def record_trace(target: list[dict[str, object]], row: dict[str, object]) -> None:
            target.append(row)
            trace_sink.emit(row)

        run_attempt(worker_a_job, FakeRuntime("runtime-A"), trace_a)
        run_attempt(
            worker_b_job,
            FakeRuntime("runtime-B", expect_first_result=True),
            trace_b,
        )

        raw_usage = list(
            pipeline.engines.conversation.meta_sqlite.iter_entity_events(
                namespace=ns.usage_events,
                from_seq=1,
            )
        )
        usage_payloads = [json.loads(row[4]) for row in raw_usage]
        maintenance_usage = [
            row
            for row in usage_payloads
            if row.get("attribution", {}).get("maintenance_job_id") == worker_a_job.job_id
        ]
        assert len(maintenance_usage) == 4
        assert {row["run_id"] for row in maintenance_usage} == {"runtime-A", "runtime-B"}
        assert sum(
            float(row["amount"])
            for row in maintenance_usage
            if row["unit"] == "total_cost"
        ) == 0.1

        usage_snapshot = pipeline.refresh_usage_projection(workspace_id)
        maintenance_aggregate = usage_snapshot.aggregates["maintenance_job"][worker_a_job.job_id]
        assert maintenance_aggregate["event_count"] == 4
        assert maintenance_aggregate["total_cost"] == 0.1

        trace_rows = trace_a + trace_b
        trace_path = tmp_path / "maintenance_worker_trace.jsonl"
        persisted_trace_rows = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(persisted_trace_rows) == len(trace_rows)
        statistics = build_maintenance_statistics(persisted_trace_rows)
        assert statistics["totals"]["attempt_count"] == 2
        assert statistics["totals"]["status_finished"] == 2
        assert [row["event"] for row in trace_rows].count("maintenance_runtime_attempt_start") == 2
        assert [row["event"] for row in trace_rows].count("maintenance_runtime_attempt_complete") == 2
        assert {row["run_id"] for row in trace_rows if row["event"] == "maintenance_runtime_attempt_complete"} == {
            "runtime-A",
            "runtime-B",
        }
        assert [row["event"] for row in trace_rows].count("maintenance_stale_claim_rejected") == 1
        assert [row["event"] for row in trace_rows].count("maintenance_job_acknowledged") == 1

        current_job = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
            namespace=maintenance_namespace,
            limit=1,
        )[0]
        assert _job_field(current_job, "status") == "DONE"

        with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
            replies = pipeline.engines.conversation.read.get_nodes(
                where={
                    "artifact_kind": "lane_message",
                    "msg_type": "reply.maintenance.completed",
                },
                limit=10,
            )
            request_nodes = pipeline.engines.conversation.read.get_nodes(
                where={
                    "artifact_kind": "lane_message",
                    "msg_type": "request.maintenance",
                },
                limit=10,
            )
            projected_replies = pipeline.engines.conversation.list_projected_lane_messages(
                inbox_id="inbox:foreground",
                msg_type="reply.maintenance.completed",
                limit=10,
            )
        assert len(replies) == 1
        assert replies[0].metadata.get("status") == "completed"
        assert len(request_nodes) == 1
        assert request_nodes[0].metadata.get("status") == "completed"
        assert len(projected_replies) == 1
        assert projected_replies[0].status == "completed"

    finally:
        in_memory_meta._now_epoch = original_now_epoch


def test_maintenance_completed_reply_cannot_be_downgraded_by_duplicate_failure(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(request)
    worker = MaintenanceWorker(pipeline.engines)
    ns = WorkspaceNamespaces(request.workspace_id)

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        request_message = pipeline.engines.conversation.read.get_nodes(
            where={
                "$and": [
                    {"artifact_kind": "lane_message"},
                    {"msg_type": "request.maintenance"},
                ],
            },
            limit=1,
        )[0]
    request_message_id = str(request_message.id)

    worker._emit_lane_reply(
        workspace_id=request.workspace_id,
        source_document_id=str(request.source_uri),
        request_node_id="request-duplicate-status",
        reply_to_message_id=request_message_id,
        status="completed",
        payload={"maintenance_kind": "distill", "run_id": "winner-run"},
    )
    worker._emit_lane_reply(
        workspace_id=request.workspace_id,
        source_document_id=str(request.source_uri),
        request_node_id="request-duplicate-status",
        reply_to_message_id=request_message_id,
        status="failed",
        payload={"maintenance_kind": "distill", "error": "late duplicate"},
    )

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        replies = pipeline.engines.conversation.read.get_nodes(
            where={
                "$and": [
                    {"artifact_kind": "lane_message"},
                    {"reply_to_message_id": request_message_id},
                ],
            },
            limit=10,
        )
        request_after = pipeline.engines.conversation.read.get_nodes(
            where={
                "$and": [
                    {"artifact_kind": "lane_message"},
                    {"msg_type": "request.maintenance"},
                ],
            },
            limit=10,
        )
        request_after = [
            node for node in request_after if str(node.id) == request_message_id
        ]
    assert [node for node in replies if node.metadata.get("msg_type") == "reply.maintenance.completed"]
    assert not [node for node in replies if node.metadata.get("msg_type") == "reply.maintenance.failed"]
    assert request_after[0].metadata.get("status") == "completed"


def test_maintenance_first_requeues_each_planner_phase_fairly(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    request = ingest_request.model_copy(update={"operation_mode": "maintenance_first"})
    artifacts = pipeline.run(request)
    parsed: list[str] = []

    def fake_parser(ctx) -> dict[str, object]:
        parsed.append(str(ctx.payload["source_document_id"]))
        return {"node_count": 3, "edge_count": 2}

    worker = MaintenanceWorker(
        pipeline.engines,
        fair_scheduling=True,
        document_parser=fake_parser,
    )
    ns = WorkspaceNamespaces(request.workspace_id)
    trace: list[dict[str, object]] = []
    worker.trace_sink = trace.append

    for _ in range(4):
        worker.process_pending_jobs(request.workspace_id)

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        status="DONE",
        limit=10,
    )
    assert len(jobs) == 1
    assert parsed == [artifacts.source_document_id]
    assert [row["next_kind"] for row in trace if row["event"] == "maintenance_plan_advanced"] == [
        "document_parse_graph",
        "document_propose_crosslinks",
        "document_validate_crosslinks",
    ]
    assert any(row["event"] == "maintenance_parse_complete" for row in trace)


def test_maintenance_worker_trace_contains_identity_thread_and_timestamp() -> None:
    events: list[dict[str, object]] = []
    worker = object.__new__(MaintenanceWorker)
    worker.worker_id = "maintenance-2"
    worker.trace_sink = events.append

    worker._emit_trace("maintenance_jobs_claimed", job_ids=["job-1"])

    assert events[0]["event"] == "maintenance_jobs_claimed"
    assert events[0]["worker_id"] == "maintenance-2"
    assert isinstance(events[0]["thread_name"], str)
    assert isinstance(events[0]["ts_ms"], int)
    assert events[0]["job_ids"] == ["job-1"]


def test_suspended_maintenance_payload_carries_checkpoint_frontier() -> None:
    captured: dict[str, object] = {}

    class FakeJobs:
        def requeue_at_tail(self, job, *, payload):
            captured["job"] = job
            captured["payload"] = payload

    worker = object.__new__(MaintenanceWorker)
    worker.engines = SimpleNamespace(conversation=SimpleNamespace(jobs=FakeJobs()))
    ctx = SimpleNamespace(
        job=SimpleNamespace(job_id="job-1"),
        job_id="job-1",
        payload={"source_document_id": "doc-1", "maintenance_kind": "document_seed_graph"},
    )
    result = SimpleNamespace(
        run_id="maintenance-run-1",
        final_state={"_rt_join": {"suspended": [["node-2", 0, "token-2", None]]}},
    )

    worker._requeue_suspended_maintenance_job(ctx, result)

    assert captured["job"].job_id == "job-1"
    assert captured["payload"] == {
        "source_document_id": "doc-1",
        "maintenance_kind": "document_seed_graph",
        "continuation_run_id": "maintenance-run-1",
        "suspended_node_id": "node-2",
        "suspended_token_id": "token-2",
    }


def test_fair_runtime_suspension_reads_job_payload_and_requeues_at_tail(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
) -> None:
    request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    artifacts = pipeline.run(request)
    worker = MaintenanceWorker(
        pipeline.engines,
        fair_scheduling=True,
        maintenance_steps_per_slice=1,
    )
    captured: dict[str, object] = {}

    class SuspendedRuntime:
        def run(self, **kwargs):
            captured["run_kwargs"] = kwargs
            return SimpleNamespace(
                status="suspended",
                run_id="maintenance-run-1",
                final_state={
                    "_rt_join": {
                        "suspended": [["next-node", 0, "next-token", None]],
                    }
                },
            )

        def resume_run(self, **kwargs):
            captured["resume_kwargs"] = kwargs
            return SimpleNamespace(status="finished", run_id="maintenance-run-1")

    monkeypatch.setattr(worker, "runtime", SuspendedRuntime())

    original_requeue = pipeline.engines.conversation.jobs.requeue_at_tail

    def capture_requeue(job, *, payload):
        captured["job"] = job
        captured["payload"] = payload
        return original_requeue(job, payload=payload)

    monkeypatch.setattr(
        pipeline.engines.conversation.jobs,
        "requeue_at_tail",
        capture_requeue,
    )

    worker.process_pending_jobs(request.workspace_id)

    run_kwargs = captured["run_kwargs"]
    assert run_kwargs["initial_state"]["request_id"]
    assert captured["payload"]["source_document_id"] == artifacts.source_document_id
    assert captured["payload"]["continuation_run_id"] == "maintenance-run-1"
    assert captured["payload"]["suspended_node_id"] == "next-node"
    assert captured["payload"]["suspended_token_id"] == "next-token"

    worker.process_pending_jobs(request.workspace_id)

    resume_kwargs = captured["resume_kwargs"]
    assert resume_kwargs["run_id"] == "maintenance-run-1"
    assert resume_kwargs["suspended_node_id"] == "next-node"
    assert resume_kwargs["suspended_token_id"] == "next-token"


def test_maintenance_flow_records_graph_native_trace(pipeline: IngestPipeline, ingest_request: IngestPipelineRequest):
    # 1. Setup - Materialize design
    materialize_maintenance_designs(pipeline.engines.workflow)
    
    # 2. Trigger Ingest - Creates maintenance request in conversation engine (conv_bg)
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    artifacts = pipeline.run(sync_request)
    
    workspace_id = sync_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=10,
    )
    assert len(jobs) == 1
    assert _job_field(jobs[0], "entity_kind") == "maintenance_job"
    assert _job_field(jobs[0], "entity_id") == artifacts.source_document_id
    assert _job_payload(jobs[0])["request_node_id"] == artifacts.maintenance_job_id
    
    # Verify request existence in conversation engine
    requests = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "namespace": ns.conv_bg,
        }
    )
    assert len(requests) == 1
    req_node = requests[0]
    assert req_node.metadata.get("status") == "pending"

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        lane_requests = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "lane_message",
                "msg_type": "request.maintenance",
            }
        )
    assert len(lane_requests) == 1
    request_message_id = str(lane_requests[0].id)

    # 3. Run Worker
    worker = MaintenanceWorker(pipeline.engines)
    worker.process_pending_jobs(workspace_id)

    done_jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        status="DONE",
        limit=10,
    )
    assert len(done_jobs) == 1
    
    # 4. Verify Final State - WorkflowRunNode Trace
    # We no longer perform CRUD updates on the request node itself.
    # The authoritative state is in the append-only traces.
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        runs = pipeline.engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(req_node.id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) == 1, f"No workflow_run found for request {req_node.id}"
        run_id = runs[0].metadata.get("run_id")
        assert run_id is not None
        
        # Verify authoritative completion event existence
        completes = pipeline.engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_completed"
            }
        )
        assert len(completes) == 1, f"No workflow_completed found for run {run_id}"

    # 5. Verify Graph-Native Trace Details
    # The runtime creates WorkflowRunNode and WorkflowStepExecNode
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        traces = pipeline.engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
            }
        )
    # Should find at least the Run node and the Step exec nodes
    kinds = [t.metadata.get("entity_type") for t in traces]
    assert "workflow_run" in kinds
    assert "workflow_step_exec" in kinds
    
    # Verify the workflow runs the explicit derived-knowledge workflow.
    node_ops = [t.metadata.get("op") for t in traces if t.metadata.get("entity_type") == "workflow_step_exec"]
    assert "distill" in node_ops
    assert "check_done" in node_ops

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        replies = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "lane_message",
                "msg_type": "reply.maintenance.completed",
            }
        )
        request_after = pipeline.engines.conversation.read.get_nodes(
            where={"artifact_kind": "lane_message"},
        )
    assert len(replies) == 1
    assert replies[0].metadata.get("reply_to_message_id") == request_message_id
    assert replies[0].metadata.get("status") == "completed"
    matching_request = [node for node in request_after if str(node.id) == request_message_id]
    assert len(matching_request) == 1
    assert matching_request[0].metadata.get("status") == "completed"


def test_maintenance_worker_uses_probe_reads_for_workflow_design_presence(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
):
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(sync_request)
    worker = MaintenanceWorker(pipeline.engines)
    calls: list[str] = []

    def fake_materialize(_workflow_engine):
        calls.append("materialize")

    monkeypatch.setattr(worker_module, "materialize_maintenance_designs", fake_materialize)
    monkeypatch.setattr(
        pipeline.engines.workflow.read,
        "get_nodes",
        lambda *args, **kwargs: pytest.fail("workflow probe should not hydrate nodes"),
    )
    monkeypatch.setattr(pipeline.engines.workflow.read, "node_exists", lambda *args, **kwargs: False)
    monkeypatch.setattr(worker.runtime, "run", lambda **kwargs: SimpleNamespace(status="finished"))

    worker.process_pending_jobs(sync_request.workspace_id)

    assert calls == ["materialize"]


def test_maintenance_worker_preserves_suspended_runtime_status(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
):
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(sync_request)
    worker = MaintenanceWorker(
        pipeline.engines,
        fair_scheduling=True,
        maintenance_steps_per_slice=1,
    )
    ns = WorkspaceNamespaces(sync_request.workspace_id)
    monkeypatch.setattr(
        worker.runtime,
        "run",
        lambda **kwargs: SimpleNamespace(
            status="suspended",
            run_id="maintenance-run-suspended",
            final_state={
                "_rt_join": {
                    "suspended": [["next-node", 0, "next-token", None]],
                }
            },
        ),
    )

    worker.process_pending_jobs(sync_request.workspace_id)

    done_jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        status="DONE",
        limit=10,
    )
    assert done_jobs == []
    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=10,
    )
    assert len(jobs) == 1
    assert _job_field(jobs[0], "status") == "PENDING"
    assert int(_job_field(jobs[0], "retry_count") or 0) == 0
    assert _job_field(jobs[0], "lease_until") is None
    payload = _job_payload(jobs[0])
    assert payload["continuation_run_id"] == "maintenance-run-suspended"
    assert payload["suspended_node_id"] == "next-node"
    assert payload["suspended_token_id"] == "next-token"

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        replies = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "lane_message",
                "msg_type": "reply.maintenance.suspended",
            },
            limit=10,
        )
    assert len(replies) == 1
    assert replies[0].metadata.get("status") == "suspended"
    payload = _lane_payload(replies[0])
    assert payload["runtime_status"] == "suspended"


def test_maintenance_worker_propagates_unrelated_workflow_lookup_error(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
):
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(sync_request)
    worker = MaintenanceWorker(pipeline.engines)

    def boom(*args, **kwargs):
        raise RuntimeError("workflow read exploded")

    monkeypatch.setattr(pipeline.engines.workflow.read, "node_exists", boom)
    monkeypatch.setattr(
        worker_module,
        "materialize_maintenance_designs",
        lambda _workflow_engine: (_ for _ in ()).throw(AssertionError("should not rematerialize")),
    )

    with pytest.raises(RuntimeError, match="workflow read exploded"):
        worker.process_pending_jobs(sync_request.workspace_id)

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=WorkspaceNamespaces(sync_request.workspace_id).maintenance_jobs,
        limit=10,
    )
    assert jobs
    assert _job_field(jobs[0], "status") != "DOING"
    assert int(_job_field(jobs[0], "retry_count") or 0) == 1


def test_maintenance_worker_accounts_early_job_processing_failure(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
):
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(sync_request)
    worker = MaintenanceWorker(pipeline.engines)

    def boom(*args, **kwargs):
        raise RuntimeError("request node exploded")

    monkeypatch.setattr(worker, "_load_request_node", boom)

    with pytest.raises(RuntimeError, match="request node exploded"):
        worker.process_pending_jobs(sync_request.workspace_id)

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=WorkspaceNamespaces(sync_request.workspace_id).maintenance_jobs,
        limit=10,
    )
    assert jobs
    assert _job_field(jobs[0], "status") != "DOING"
    assert int(_job_field(jobs[0], "retry_count") or 0) == 1


def test_maintenance_worker_reply_emission_is_idempotent(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
):
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(sync_request)
    worker = MaintenanceWorker(pipeline.engines)
    ns = WorkspaceNamespaces(sync_request.workspace_id)

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        requests = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "lane_message",
                "msg_type": "request.maintenance",
            }
        )
    assert len(requests) == 1
    request_message_id = str(requests[0].id)

    worker._emit_lane_reply(
        workspace_id=sync_request.workspace_id,
        source_document_id=str(sync_request.source_uri),
        request_node_id="req-1",
        reply_to_message_id=request_message_id,
        status="completed",
        payload={"maintenance_kind": "distill"},
    )
    worker._emit_lane_reply(
        workspace_id=sync_request.workspace_id,
        source_document_id=str(sync_request.source_uri),
        request_node_id="req-1",
        reply_to_message_id=request_message_id,
        status="completed",
        payload={"maintenance_kind": "distill"},
    )

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        replies = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "lane_message",
                "msg_type": "reply.maintenance.completed",
            }
        )
    assert len(replies) == 1
    assert replies[0].metadata.get("status") == "completed"


def test_maintenance_worker_reply_lookup_converges_after_pre_ack_redelivery(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
):
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(sync_request)
    worker = MaintenanceWorker(pipeline.engines)
    ns = WorkspaceNamespaces(sync_request.workspace_id)

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        request_message = pipeline.engines.conversation.read.get_nodes(
            where={
                "$and": [
                    {"artifact_kind": "lane_message"},
                    {"msg_type": "request.maintenance"},
                ],
            },
            limit=1,
        )[0]
    request_message_id = str(request_message.id)

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        pipeline.engines.conversation.send_lane_message(
            conversation_id=f"maintenance:{sync_request.source_uri}",
            inbox_id="inbox:foreground",
            sender_id="lane:worker:maintenance",
            recipient_id="lane:foreground",
            msg_type="reply.maintenance.completed",
            payload={
                "workspace_id": sync_request.workspace_id,
                "request_node_id": "req-1",
                "maintenance_kind": "distill",
            },
            reply_to=request_message_id,
            correlation_id=request_message_id,
            idempotency_key="legacy-or-pre-ack-reply",
        )

    worker._emit_lane_reply(
        workspace_id=sync_request.workspace_id,
        source_document_id=str(sync_request.source_uri),
        request_node_id="req-1",
        reply_to_message_id=request_message_id,
        status="completed",
        payload={"maintenance_kind": "distill"},
    )

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        replies = pipeline.engines.conversation.read.get_nodes(
            where={
                "$and": [
                    {"artifact_kind": "lane_message"},
                    {"msg_type": "reply.maintenance.completed"},
                    {"reply_to_message_id": request_message_id},
                    {"correlation_id": request_message_id},
                ],
            },
            limit=10,
        )
    assert len(replies) == 1


def test_maintenance_worker_failed_reply_emission_is_idempotent(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
):
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(sync_request)
    worker = MaintenanceWorker(pipeline.engines)
    ns = WorkspaceNamespaces(sync_request.workspace_id)

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        request_message = pipeline.engines.conversation.read.get_nodes(
            where={
                "$and": [
                    {"artifact_kind": "lane_message"},
                    {"msg_type": "request.maintenance"},
                ],
            },
            limit=1,
        )[0]
    request_message_id = str(request_message.id)

    worker._emit_lane_reply(
        workspace_id=sync_request.workspace_id,
        source_document_id=str(sync_request.source_uri),
        request_node_id="req-1",
        reply_to_message_id=request_message_id,
        status="failed",
        payload={"maintenance_kind": "distill", "error": "boom"},
    )
    worker._emit_lane_reply(
        workspace_id=sync_request.workspace_id,
        source_document_id=str(sync_request.source_uri),
        request_node_id="req-1",
        reply_to_message_id=request_message_id,
        status="failed",
        payload={"maintenance_kind": "distill", "error": "boom"},
    )

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        replies = pipeline.engines.conversation.read.get_nodes(
            where={
                "$and": [
                    {"artifact_kind": "lane_message"},
                    {"msg_type": "reply.maintenance.failed"},
                    {"reply_to_message_id": request_message_id},
                    {"correlation_id": request_message_id},
                ],
            },
            limit=10,
        )
    assert len(replies) == 1


def test_maintenance_enqueue_requires_durable_queue_support(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
):
    request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    monkeypatch.setattr(
        pipeline.engines.conversation.meta_sqlite,
        "enqueue_index_job",
        None,
        raising=False,
    )

    with pytest.raises(DurableQueueUnavailableError, match="enqueue_index_job"):
        pipeline._enqueue_maintenance_job(
            request=request,
            request_node_id="req-queue-missing",
            source_document_id="doc-queue-missing",
            namespace=WorkspaceNamespaces(request.workspace_id).maintenance_jobs,
            lane_message_id="lane:queue-missing",
        )


def test_projection_enqueue_requires_durable_queue_support(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
):
    request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    monkeypatch.setattr(
        pipeline.engines.conversation.meta_sqlite,
        "enqueue_index_job",
        None,
        raising=False,
    )

    with pytest.raises(DurableQueueUnavailableError, match="enqueue_index_job"):
        pipeline._enqueue_projection_job(
            request=request,
            promoted_id="promoted:queue-missing",
            namespace=WorkspaceNamespaces(request.workspace_id).projection_jobs,
        )


def test_maintenance_worker_requires_durable_queue_claim_support(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
):
    request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(request)
    monkeypatch.setattr(
        pipeline.engines.conversation.meta_sqlite,
        "claim_index_jobs",
        None,
        raising=False,
    )

    with pytest.raises(DurableQueueUnavailableError, match="claim_index_jobs"):
        MaintenanceWorker(pipeline.engines).process_pending_jobs(request.workspace_id)


def test_projection_worker_requires_durable_queue_claim_support(
    pipeline: IngestPipeline,
    ingest_request: IngestPipelineRequest,
    monkeypatch,
    tmp_path: Path,
):
    request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    pipeline.run(request)
    monkeypatch.setattr(
        pipeline.engines.conversation.meta_sqlite,
        "claim_index_jobs",
        None,
        raising=False,
    )
    vault_root = tmp_path / "projection-queue-missing"
    vault_root.mkdir()

    with pytest.raises(DurableQueueUnavailableError, match="claim_index_jobs"):
        ProjectionWorker(pipeline.engines).process_pending_projections(
            request.workspace_id,
            str(vault_root),
        )
