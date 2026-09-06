"""Durable background execution for Codex-mode workbench turns."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping

from kogwistar.engine_core.jobs import JobQueueItem
from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[], None]
ExecuteTurn = Callable[[Mapping[str, object], ProgressCallback], Mapping[str, object]]
TraceSink = Callable[[dict[str, object]], None]

_ARTIFACT_LOCKS: dict[tuple[int, str, str], threading.Lock] = {}
_ARTIFACT_LOCKS_GUARD = threading.Lock()


def _artifact_lock(engines: NamespaceEngines, namespace: str, node_id: str) -> threading.Lock:
    key = (id(engines.conversation), namespace, node_id)
    with _ARTIFACT_LOCKS_GUARD:
        lock = _ARTIFACT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _ARTIFACT_LOCKS[key] = lock
        return lock


@dataclass(frozen=True, slots=True)
class WorkbenchInteraction:
    interaction_id: str
    workspace_id: str
    session_id: str
    status: str
    submitted_at_ms: int
    completed_at_ms: int | None = None
    response: dict[str, object] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "interaction_id": self.interaction_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "status": self.status,
            "submitted_at_ms": self.submitted_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "response": self.response,
            "error": self.error,
        }


class WorkbenchInteractionStore:
    """Append-only request/result artifacts over the conversation graph."""

    request_kind = "workbench_interaction_request"
    result_kind = "workbench_interaction_result"
    confirmation_kind = "workbench_interaction_confirmation"

    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def enqueue(self, payload: Mapping[str, object], *, max_retries: int = 3) -> WorkbenchInteraction:
        workspace_id = str(payload["workspace_id"])
        session_id = str(payload.get("session_id") or "default")
        interaction_id = str(payload.get("interaction_id") or uuid.uuid4())
        submitted_at_ms = int(payload.get("submitted_at_ms") or int(time.time() * 1000))
        stored_payload = {
            **dict(payload),
            "interaction_id": interaction_id,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "submitted_at_ms": submitted_at_ms,
        }
        self._write_artifact(
            workspace_id=workspace_id,
            node_id=_request_node_id(workspace_id, interaction_id),
            artifact_kind=self.request_kind,
            interaction_id=interaction_id,
            payload=stored_payload,
        )
        namespace = WorkspaceNamespaces(workspace_id).workbench_jobs
        self.engines.conversation.jobs.require_available(enqueue=True)
        job_id = self.engines.conversation.jobs.enqueue(
            job_id=interaction_id,
            namespace=namespace,
            entity_kind="workbench_interaction",
            entity_id=interaction_id,
            job_kind="codex_workbench_turn",
            payload=stored_payload,
            max_retries=max(1, int(max_retries)),
        )
        if not job_id:
            raise RuntimeError("durable workbench queue did not return a job ID")
        return WorkbenchInteraction(
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            session_id=session_id,
            status="pending",
            submitted_at_ms=submitted_at_ms,
        )

    def get(self, *, workspace_id: str, interaction_id: str) -> WorkbenchInteraction | None:
        result = self._read_artifact(workspace_id=workspace_id, node_id=_result_node_id(workspace_id, interaction_id))
        if result is not None:
            return _interaction_from_payload(result)
        request = self._read_artifact(workspace_id=workspace_id, node_id=_request_node_id(workspace_id, interaction_id))
        if request is None:
            return None
        return WorkbenchInteraction(
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            session_id=str(request.get("session_id") or "default"),
            status="pending",
            submitted_at_ms=int(request.get("submitted_at_ms") or 0),
        )

    def persist_result(
        self,
        *,
        workspace_id: str,
        interaction_id: str,
        session_id: str,
        submitted_at_ms: int,
        response: Mapping[str, object] | None = None,
        error: str | None = None,
    ) -> tuple[WorkbenchInteraction, bool]:
        """Persist the first terminal result and report whether this call won."""
        existing = self.get(workspace_id=workspace_id, interaction_id=interaction_id)
        if existing is not None and existing.status in {"completed", "failed"}:
            return existing, False
        interaction = WorkbenchInteraction(
            interaction_id=interaction_id,
            workspace_id=workspace_id,
            session_id=session_id,
            status="failed" if error else "completed",
            submitted_at_ms=submitted_at_ms,
            completed_at_ms=int(time.time() * 1000),
            response=dict(response) if response is not None else None,
            error=error,
        )
        created = self._write_artifact(
            workspace_id=workspace_id,
            node_id=_result_node_id(workspace_id, interaction_id),
            artifact_kind=self.result_kind,
            interaction_id=interaction_id,
            payload=interaction.to_dict(),
        )
        if created:
            return interaction, True
        winner = self.get(workspace_id=workspace_id, interaction_id=interaction_id)
        return (winner or interaction), False

    def get_confirmation(self, *, workspace_id: str, interaction_id: str) -> dict[str, object] | None:
        return self._read_artifact(
            workspace_id=workspace_id,
            node_id=_confirmation_node_id(workspace_id, interaction_id),
        )

    def persist_confirmation(
        self,
        *,
        workspace_id: str,
        interaction_id: str,
        payload: Mapping[str, object],
    ) -> tuple[dict[str, object], bool]:
        created = self._write_artifact(
            workspace_id=workspace_id,
            node_id=_confirmation_node_id(workspace_id, interaction_id),
            artifact_kind=self.confirmation_kind,
            interaction_id=interaction_id,
            payload=payload,
        )
        if created:
            return dict(payload), True
        return self.get_confirmation(workspace_id=workspace_id, interaction_id=interaction_id) or dict(payload), False

    def _write_artifact(
        self,
        *,
        workspace_id: str,
        node_id: str,
        artifact_kind: str,
        interaction_id: str,
        payload: Mapping[str, object],
    ) -> bool:
        namespace = WorkspaceNamespaces(workspace_id).conversation_fg_space
        with _artifact_lock(self.engines, namespace, node_id):
            with _temporary_namespace(self.engines.conversation, namespace):
                if self.engines.conversation.read.get_nodes(ids=[node_id], limit=1):
                    return False
                span = Span.from_dummy_for_conversation(f"workbench:{interaction_id}")
                self.engines.conversation.write.add_node(
                    Node(
                        id=node_id,
                        label=f"Workbench interaction: {artifact_kind}",
                        type="entity",
                        summary=str(payload.get("query_text") or payload.get("status") or artifact_kind),
                        doc_id=f"_conv:{interaction_id}",
                        mentions=[Grounding(spans=[span])],
                        metadata={
                            "workspace_id": workspace_id,
                            "graph_space": "conversation",
                            "graph_lane": "foreground",
                            "artifact_kind": artifact_kind,
                            "interaction_id": interaction_id,
                            "interaction_payload_json": json.dumps(payload, sort_keys=True, default=str),
                        },
                    )
                )
        return True

    def _read_artifact(self, *, workspace_id: str, node_id: str) -> dict[str, object] | None:
        namespace = WorkspaceNamespaces(workspace_id).conversation_fg_space
        with _temporary_namespace(self.engines.conversation, namespace):
            nodes = self.engines.conversation.read.get_nodes(ids=[node_id], limit=1)
        if not nodes:
            return None
        raw = dict(nodes[0].metadata or {}).get("interaction_payload_json")
        if not isinstance(raw, str):
            return None
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("invalid workbench interaction artifact node_id=%s", node_id)
            return None
        return dict(decoded) if isinstance(decoded, Mapping) else None


class CodexWorkbenchWorker:
    """Execute durable Codex jobs while the claim token remains authoritative."""

    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        execute_turn: ExecuteTurn,
        worker_id: str | None = None,
        lease_seconds: int = 150,
        lease_renew_interval_seconds: int = 30,
        progress_grace_seconds: int = 90,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self.engines = engines
        self.execute_turn = execute_turn
        self.worker_id = str(worker_id or f"codex-workbench-{uuid.uuid4().hex[:10]}")
        self.lease_seconds = max(1, int(lease_seconds))
        self.lease_renew_interval_seconds = max(1, int(lease_renew_interval_seconds))
        self.progress_grace_seconds = max(1, int(progress_grace_seconds))
        self.trace_sink = trace_sink
        self.store = WorkbenchInteractionStore(engines)

    def clone(self, suffix: str) -> "CodexWorkbenchWorker":
        return CodexWorkbenchWorker(
            self.engines,
            execute_turn=self.execute_turn,
            worker_id=f"{self.worker_id}-{suffix}",
            lease_seconds=self.lease_seconds,
            lease_renew_interval_seconds=self.lease_renew_interval_seconds,
            progress_grace_seconds=self.progress_grace_seconds,
            trace_sink=self.trace_sink,
        )

    def process_pending_jobs(self, workspace_id: str, *, limit: int = 1) -> int:
        namespace = WorkspaceNamespaces(workspace_id).workbench_jobs
        self.engines.conversation.jobs.require_available(claim=True)
        jobs = self.engines.conversation.jobs.claim(
            namespace=namespace,
            limit=max(1, int(limit)),
            lease_seconds=self.lease_seconds,
        )
        for job in jobs:
            self._handle_job(job)
        return len(jobs)

    def _handle_job(self, job: JobQueueItem) -> None:
        stop = threading.Event()
        claim_lost = threading.Event()
        progress_lock = threading.Lock()
        last_progress = [time.monotonic()]

        def progress() -> None:
            with progress_lock:
                last_progress[0] = time.monotonic()
            self._trace("codex_turn_progress", job_id=job.job_id)

        renewer = threading.Thread(
            target=self._renew_while_progressing,
            args=(job, last_progress, progress_lock, stop, claim_lost),
            name=f"{self.worker_id}-lease",
            daemon=True,
        )
        renewer.start()
        self._trace("codex_turn_started", job_id=job.job_id, workspace_id=job.payload.get("workspace_id"))
        try:
            response = dict(self.execute_turn(job.payload, progress))
            progress()
            if claim_lost.is_set() or not self.engines.conversation.jobs.renew_lease(job, lease_seconds=self.lease_seconds):
                self._trace("codex_turn_stale_result_dropped", job_id=job.job_id)
                return
            interaction, created = self.store.persist_result(
                workspace_id=str(job.payload["workspace_id"]),
                interaction_id=str(job.payload["interaction_id"]),
                session_id=str(job.payload.get("session_id") or "default"),
                submitted_at_ms=int(job.payload.get("submitted_at_ms") or 0),
                response=response,
            )
            acknowledged = self.engines.conversation.jobs.mark_done(job.job_id, claim_token=job.claim_token)
            event = "codex_turn_completed" if acknowledged and created else "codex_turn_duplicate_result_ignored"
            self._trace(event, job_id=job.job_id, interaction_id=interaction.interaction_id)
        except Exception as exc:
            final_failure = int(job.retry_count) + 1 >= int(job.max_retries)
            owns_claim = not claim_lost.is_set() and self.engines.conversation.jobs.renew_lease(
                job,
                lease_seconds=self.lease_seconds,
            )
            if final_failure and owns_claim:
                _, created = self.store.persist_result(
                    workspace_id=str(job.payload["workspace_id"]),
                    interaction_id=str(job.payload["interaction_id"]),
                    session_id=str(job.payload.get("session_id") or "default"),
                    submitted_at_ms=int(job.payload.get("submitted_at_ms") or 0),
                    error=f"{type(exc).__name__}: {exc}",
                )
                if not created:
                    self._trace("codex_turn_duplicate_failure_ignored", job_id=job.job_id)
            if owns_claim:
                self.engines.conversation.jobs.retry_or_fail(job, exc)
            else:
                self._trace("codex_turn_stale_failure_dropped", job_id=job.job_id)
            self._trace("codex_turn_failed", job_id=job.job_id, error=f"{type(exc).__name__}: {exc}")
        finally:
            stop.set()
            renewer.join(timeout=2)

    def _renew_while_progressing(
        self,
        job: JobQueueItem,
        last_progress: list[float],
        progress_lock: threading.Lock,
        stop: threading.Event,
        claim_lost: threading.Event,
    ) -> None:
        while not stop.wait(self.lease_renew_interval_seconds):
            with progress_lock:
                silent_seconds = time.monotonic() - last_progress[0]
            if silent_seconds > self.progress_grace_seconds:
                self._trace(
                    "codex_lease_renewal_stopped",
                    job_id=job.job_id,
                    reason="no_progress",
                    silent_seconds=round(silent_seconds, 3),
                )
                return
            if not self.engines.conversation.jobs.renew_lease(job, lease_seconds=self.lease_seconds):
                claim_lost.set()
                self._trace("codex_lease_ownership_lost", job_id=job.job_id)
                return
            self._trace("codex_lease_renewed", job_id=job.job_id)

    def _trace(self, event: str, **fields: object) -> None:
        payload = {"event": event, "worker_id": self.worker_id, "at_ms": int(time.time() * 1000), **fields}
        logger.info("%s %s", event, payload)
        if self.trace_sink is not None:
            self.trace_sink(payload)


class CodexWorkbenchDispatcher:
    """Bounded worker pool backed by the durable workbench queue."""

    def __init__(self, worker: CodexWorkbenchWorker, *, worker_count: int = 1) -> None:
        count = max(1, int(worker_count))
        self._workers = [worker, *(worker.clone(str(index)) for index in range(2, count + 1))]
        self._executor = ThreadPoolExecutor(max_workers=count, thread_name_prefix="llm-wiki-codex")
        self._closed = False
        self._lock = threading.Lock()
        self._active: dict[str, int] = {}
        self._rerun: set[str] = set()

    def notify(self, workspace_id: str) -> None:
        should_start = False
        with self._lock:
            if self._closed:
                raise RuntimeError("Codex workbench dispatcher is closed")
            if self._active.get(workspace_id, 0):
                self._rerun.add(workspace_id)
                return
            self._active[workspace_id] = len(self._workers)
            should_start = True
        if should_start:
            self._start(workspace_id)

    def _start(self, workspace_id: str) -> None:
        # A future may complete before add_done_callback returns, so neither
        # submission nor callback registration may occur under _lock.
        for worker in self._workers:
            future = self._executor.submit(self._drain, worker, workspace_id)
            future.add_done_callback(lambda completed, workspace=workspace_id: self._worker_done(workspace, completed))

    def recover(self, workspace_id: str) -> None:
        self.notify(workspace_id)

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    @staticmethod
    def _drain(worker: CodexWorkbenchWorker, workspace_id: str) -> None:
        while worker.process_pending_jobs(workspace_id, limit=1):
            pass

    def _worker_done(self, workspace_id: str, future: Future[None]) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Codex workbench worker drain failed workspace_id=%s", workspace_id)
        restart = False
        with self._lock:
            remaining = self._active.get(workspace_id, 1) - 1
            if remaining > 0:
                self._active[workspace_id] = remaining
                return
            self._active.pop(workspace_id, None)
            if workspace_id in self._rerun and not self._closed:
                self._rerun.discard(workspace_id)
                self._active[workspace_id] = len(self._workers)
                restart = True
        if restart:
            self._start(workspace_id)


def _request_node_id(workspace_id: str, interaction_id: str) -> str:
    return str(stable_id("kogwistar_llm_wiki.workbench_request", workspace_id, interaction_id))


def _result_node_id(workspace_id: str, interaction_id: str) -> str:
    return str(stable_id("kogwistar_llm_wiki.workbench_result", workspace_id, interaction_id))


def _interaction_from_payload(payload: Mapping[str, object]) -> WorkbenchInteraction:
    response = payload.get("response")
    completed = payload.get("completed_at_ms")
    return WorkbenchInteraction(
        interaction_id=str(payload["interaction_id"]),
        workspace_id=str(payload["workspace_id"]),
        session_id=str(payload.get("session_id") or "default"),
        status=str(payload.get("status") or "pending"),
        submitted_at_ms=int(payload.get("submitted_at_ms") or 0),
        completed_at_ms=None if completed is None else int(completed),
        response=dict(response) if isinstance(response, Mapping) else None,
        error=str(payload.get("error") or "") or None,
    )


def _confirmation_node_id(workspace_id: str, interaction_id: str) -> str:
    return f"workbench_confirmation:{stable_id('workbench_confirmation', workspace_id, interaction_id)}"


__all__ = [
    "CodexWorkbenchDispatcher",
    "CodexWorkbenchWorker",
    "ExecuteTurn",
    "ProgressCallback",
    "WorkbenchInteraction",
    "WorkbenchInteractionStore",
]
