from __future__ import annotations

import threading
import time

from kogwistar_llm_wiki import build_in_memory_namespace_engines
from kogwistar_llm_wiki.ingest_pipeline import build_persistent_namespace_engines
from kogwistar_llm_wiki.workbench_background import (
    CodexWorkbenchDispatcher,
    CodexWorkbenchWorker,
    WorkbenchInteractionStore,
)


def _payload(workspace_id: str, interaction_id: str) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "interaction_id": interaction_id,
        "session_id": "browser-1",
        "query_text": f"question {interaction_id}",
        "mode": "codex",
    }


def test_worker_persists_one_terminal_result_and_acknowledges_job():
    engines = build_in_memory_namespace_engines()
    try:
        store = WorkbenchInteractionStore(engines)
        store.enqueue(_payload("background-test", "turn-1"))
        worker = CodexWorkbenchWorker(
            engines,
            execute_turn=lambda payload, progress: {"answer": str(payload["query_text"])},
        )

        assert worker.process_pending_jobs("background-test") == 1
        result = store.get(workspace_id="background-test", interaction_id="turn-1")
        assert result is not None
        assert result.status == "completed"
        assert result.response == {"answer": "question turn-1"}
        assert worker.process_pending_jobs("background-test") == 0
    finally:
        engines.close()


def test_two_workers_claim_distinct_turns_without_duplicate_execution():
    engines = build_in_memory_namespace_engines()
    dispatcher = None
    try:
        store = WorkbenchInteractionStore(engines)
        observed: list[str] = []
        lock = threading.Lock()

        def execute(payload, progress):
            progress()
            with lock:
                observed.append(str(payload["interaction_id"]))
            time.sleep(0.05)
            progress()
            return {"interaction": payload["interaction_id"]}

        worker = CodexWorkbenchWorker(engines, execute_turn=execute, worker_id="worker")
        dispatcher = CodexWorkbenchDispatcher(worker, worker_count=2)
        for interaction_id in ("turn-a", "turn-b", "turn-c"):
            store.enqueue(_payload("parallel-test", interaction_id))
        dispatcher.notify("parallel-test")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            results = [store.get(workspace_id="parallel-test", interaction_id=value) for value in observed]
            if len(observed) == 3 and all(result and result.status == "completed" for result in results):
                break
            time.sleep(0.02)
        assert sorted(observed) == ["turn-a", "turn-b", "turn-c"]
        assert len(observed) == len(set(observed))
    finally:
        if dispatcher is not None:
            dispatcher.close()
        engines.close()


def test_lost_claim_drops_late_result_instead_of_overwriting(monkeypatch):
    engines = build_in_memory_namespace_engines()
    traces: list[dict[str, object]] = []
    try:
        store = WorkbenchInteractionStore(engines)
        store.enqueue(_payload("stale-test", "turn-stale"))
        worker = CodexWorkbenchWorker(
            engines,
            execute_turn=lambda payload, progress: {"late": True},
            trace_sink=traces.append,
        )
        monkeypatch.setattr(engines.conversation.jobs, "renew_lease", lambda *args, **kwargs: False)

        assert worker.process_pending_jobs("stale-test") == 1
        result = store.get(workspace_id="stale-test", interaction_id="turn-stale")
        assert result is not None
        assert result.status == "pending"
        assert any(item["event"] == "codex_turn_stale_result_dropped" for item in traces)
    finally:
        engines.close()


def test_final_worker_failure_is_queryable_and_preserves_request_history():
    engines = build_in_memory_namespace_engines()
    try:
        store = WorkbenchInteractionStore(engines)
        store.enqueue(_payload("failure-test", "turn-failed"), max_retries=1)

        def fail(_payload, _progress):
            raise ValueError("fake model failure")

        worker = CodexWorkbenchWorker(engines, execute_turn=fail)
        assert worker.process_pending_jobs("failure-test") == 1
        result = store.get(workspace_id="failure-test", interaction_id="turn-failed")
        assert result is not None
        assert result.status == "failed"
        assert result.error == "ValueError: fake model failure"
    finally:
        engines.close()


def test_lost_claim_cannot_publish_a_terminal_failure(monkeypatch):
    engines = build_in_memory_namespace_engines()
    traces: list[dict[str, object]] = []
    try:
        store = WorkbenchInteractionStore(engines)
        store.enqueue(_payload("stale-failure-test", "turn-stale-failure"), max_retries=1)

        def fail(_payload, _progress):
            raise ValueError("late failure")

        worker = CodexWorkbenchWorker(engines, execute_turn=fail, trace_sink=traces.append)
        monkeypatch.setattr(engines.conversation.jobs, "renew_lease", lambda *args, **kwargs: False)
        assert worker.process_pending_jobs("stale-failure-test") == 1
        result = store.get(workspace_id="stale-failure-test", interaction_id="turn-stale-failure")
        assert result is not None
        assert result.status == "pending"
        assert any(item["event"] == "codex_turn_stale_failure_dropped" for item in traces)
    finally:
        engines.close()


def test_pending_interaction_recovers_after_process_style_engine_restart(tmp_path):
    base_dir = tmp_path / "persistent-workbench"
    first = build_persistent_namespace_engines(base_dir=base_dir)
    store = WorkbenchInteractionStore(first)
    store.enqueue(_payload("restart-test", "turn-resume"))
    first.close()

    restarted = build_persistent_namespace_engines(base_dir=base_dir)
    try:
        worker = CodexWorkbenchWorker(
            restarted,
            execute_turn=lambda payload, progress: {"resumed": payload["interaction_id"]},
        )
        assert worker.process_pending_jobs("restart-test") == 1
        result = WorkbenchInteractionStore(restarted).get(
            workspace_id="restart-test",
            interaction_id="turn-resume",
        )
        assert result is not None
        assert result.status == "completed"
        assert result.response == {"resumed": "turn-resume"}
    finally:
        restarted.close()


def test_concurrent_terminal_writes_have_one_winner():
    engines = build_in_memory_namespace_engines()
    try:
        store = WorkbenchInteractionStore(engines)
        store.enqueue(_payload("concurrent-write-test", "turn-same"))
        barrier = threading.Barrier(2)
        winners: list[bool] = []

        def write_result() -> None:
            barrier.wait()
            _, created = store.persist_result(
                workspace_id="concurrent-write-test",
                interaction_id="turn-same",
                session_id="browser-1",
                submitted_at_ms=1,
                response={"answer": "one"},
            )
            winners.append(created)

        threads = [threading.Thread(target=write_result) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert sorted(winners) == [False, True]
    finally:
        engines.close()
