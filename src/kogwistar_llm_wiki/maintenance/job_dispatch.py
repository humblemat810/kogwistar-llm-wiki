"""Maintenance job dispatch and lease lifecycle orchestration."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping

from kogwistar.engine_core.jobs import JobQueueItem

from ..maintenance import (
    MaintenanceJobExecutionContext,
    MaintenanceStrategy,
    is_execution_wisdom_kind,
)
from ..maintenance.maintenance_context import maintenance_execution_context
from ..configuration.identity import durable_claims_context

logger = logging.getLogger(__name__)


class MaintenanceJobDispatchMixin:
    """Dispatch claimed jobs through the selected bounded strategy."""

    def _handle_job(self, workspace_id: str, job: JobQueueItem) -> None:
        payload = getattr(job, "payload", {})
        claims = payload.get("authority_claims") if isinstance(payload, Mapping) else None
        with durable_claims_context(claims):
            self._handle_job_with_authority(workspace_id, job)

    def _handle_job_with_authority(self, workspace_id: str, job: JobQueueItem) -> None:
        job = self.engines.conversation.jobs.coerce(job)
        job_id = str(job.job_id)
        payload = dict(job.payload)
        mode = str(payload.get("mode") or "request")
        req_node_id = str(payload.get("request_node_id") or job_id)
        lane_message_id = str(payload.get("lane_message_id") or "")
        maintenance_kind = str(payload.get("maintenance_kind") or "distill")
        request_node = self._load_request_node(workspace_id, req_node_id)
        if request_node is not None:
            maintenance_kind = str(
                payload.get("maintenance_kind")
                or getattr(request_node, "metadata", {}).get("maintenance_kind")
                or "distill"
            )
        logger.info("Processing maintenance job %s", req_node_id)
        job_started_ms = int(time.time() * 1000)
        self._emit_trace(
            "maintenance_job_start",
            workspace_id=workspace_id,
            source_document_id=str(payload.get("source_document_id") or ""),
            request_node_id=req_node_id,
            job_id=job_id,
            maintenance_kind=maintenance_kind,
            continuation=bool(payload.get("continuation_run_id")),
            lease_seconds=self.lease_seconds,
        )
        ctx = MaintenanceJobExecutionContext(
            workspace_id=workspace_id,
            job=job,
            job_id=job_id,
            payload=payload,
            request_node=request_node,
            request_node_id=req_node_id,
            lane_message_id=lane_message_id,
            maintenance_kind=maintenance_kind,
        )
        if mode == "request":
            self._attach_request_selection(ctx)
        # History-wide wisdom extraction has no source revision to fence. It
        # intentionally consumes workflow history across the workspace, while
        # source-dependent strategies must still pass the revision/readiness
        # guard before touching graph state.
        if not is_execution_wisdom_kind(maintenance_kind) and mode != "background":
            decision = self._evaluate_maintenance_guard(ctx)
            if decision.status != "ready":
                self._block_guarded_job(ctx, decision)
                self._emit_trace(
                    "maintenance_job_blocked",
                    workspace_id=workspace_id,
                    source_document_id=str(payload.get("source_document_id") or ""),
                    request_node_id=req_node_id,
                    job_id=job_id,
                    reason=decision.reason,
                    duration_ms=int(time.time() * 1000) - job_started_ms,
                )
                return
        strategy: MaintenanceStrategy = self.strategy_registry.resolve(maintenance_kind)
        self._emit_trace(
            "maintenance_strategy_start",
            workspace_id=workspace_id,
            source_document_id=str(payload.get("source_document_id") or ""),
            request_node_id=req_node_id,
            job_id=job_id,
            maintenance_kind=maintenance_kind,
        )
        lease_stop = threading.Event()
        self._claim_lost.clear()
        lease_thread = threading.Thread(
            target=self._renew_claim_while_progressing,
            args=(ctx, lease_stop),
            name=f"{self.worker_id}-lease",
            daemon=True,
        )
        lease_thread.start()
        try:
            with maintenance_execution_context():
                strategy.handle(self, ctx)
        finally:
            lease_stop.set()
            lease_thread.join(timeout=2)
            self._emit_trace(
                "maintenance_job_dispatch_complete",
                workspace_id=workspace_id,
                source_document_id=str(payload.get("source_document_id") or ""),
                request_node_id=req_node_id,
                job_id=job_id,
                maintenance_kind=maintenance_kind,
                duration_ms=int(time.time() * 1000) - job_started_ms,
            )


__all__ = ["MaintenanceJobDispatchMixin"]
