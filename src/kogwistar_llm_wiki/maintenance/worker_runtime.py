"""Lease, usage, reply, and trace mechanics for the maintenance worker."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Mapping

from kogwistar.id_provider import stable_id
from kogwistar.runtime import RunResult
from kogwistar.runtime.budget import StateBackedBudgetLedger
from kogwistar.runtime.budget_adapters import summarize_budget_events
from kogwistar.runtime.models import RunSuccess

from ..configuration.workspace import WorkspaceNamespaces
from ..usage.events import persist_usage_events
from ..usage.projection_engine import UsageProjection
from ..utils import _temporary_namespace
from .maintenance_strategies import MaintenanceJobExecutionContext
from .state import persisted_budget_state as _persisted_budget_state

logger = logging.getLogger(__name__)


class MaintenanceRuntimeWorkerMixin:
    """Shared durable runtime mechanics used by all maintenance strategies."""

    @staticmethod
    def _selection_result_payload(ctx: MaintenanceJobExecutionContext) -> dict[str, object]:
        """Return bounded selection data for durable lane replies."""
        candidates = list(ctx.payload.get("maintenance_candidates") or [])
        return {
            "selection_strategy": str(ctx.payload.get("selection_strategy") or ""),
            "maintenance_candidates": candidates[:24],
        }

    def _renew_claim_while_progressing(
        self, ctx: MaintenanceJobExecutionContext, stop: threading.Event
    ) -> None:
        """Renew only an owned, unexpired lease while traces show progress."""
        while not stop.wait(self.lease_renew_interval_seconds):
            idle_seconds = time.monotonic() - self._last_progress_monotonic
            if idle_seconds > self.lease_progress_grace_seconds:
                self._emit_trace(
                    "maintenance_lease_renewal_stopped",
                    job_id=ctx.job_id,
                    reason="no_progress",
                    idle_seconds=round(idle_seconds, 2),
                )
                self._claim_lost.set()
                return
            try:
                renewed = self.engines.conversation.jobs.renew_lease(
                    ctx.job, lease_seconds=self.lease_seconds
                )
            except Exception as exc:  # noqa: BLE001
                self._emit_trace(
                    "maintenance_lease_renewal_failed",
                    job_id=ctx.job_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                self._claim_lost.set()
                return
            if not renewed:
                self._emit_trace(
                    "maintenance_stale_claim_rejected",
                    job_id=ctx.job_id,
                    reason="lease_ownership_lost",
                )
                self._claim_lost.set()
                return
            self._emit_trace(
                "maintenance_lease_renewed",
                job_id=ctx.job_id,
                lease_seconds=self.lease_seconds,
                idle_seconds=round(idle_seconds, 2),
            )

    def _persist_maintenance_usage(
        self,
        ctx: MaintenanceJobExecutionContext,
        budget_ledger: StateBackedBudgetLedger,
        result: object | None,
    ) -> None:
        ns = WorkspaceNamespaces(ctx.workspace_id)
        persist_usage_events(
            self.engines.conversation.meta_sqlite,
            namespace=ns.usage_events,
            events=budget_ledger.events,
            workspace_id=ctx.workspace_id,
            attempt_id=str(getattr(result, "run_id", "") or uuid.uuid4()),
            source_document_id=str(ctx.payload.get("source_document_id") or "") or None,
            operation_id=str(ctx.job_id or ctx.request_node_id),
            operation_kind=ctx.maintenance_kind,
            maintenance_job_id=str(ctx.job_id or ctx.request_node_id),
            provider=self.provider_settings.parser.provider,
            model=self.provider_settings.parser.model,
        )
        UsageProjection(
            self.engines.conversation.meta_sqlite,
            workspace_id=ctx.workspace_id,
            source_namespace=ns.usage_events,
            projection_namespace=ns.usage_projection,
        ).refresh()
        if self.usage_sink is not None:
            summary = summarize_budget_events(budget_ledger.events)
            input_tokens = float(summary.get("input_tokens", 0) or 0)
            output_tokens = float(summary.get("output_tokens", 0) or 0)
            self.usage_sink(
                f"{ctx.job_id}:{getattr(result, 'run_id', '') or ctx.request_node_id}",
                {
                    "tokens": float(summary.get("total_tokens", input_tokens + output_tokens) or 0),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "money": float(summary.get("total_cost", 0) or 0),
                },
            )

    def _requeue_suspended_maintenance_job(
        self,
        ctx: MaintenanceJobExecutionContext,
        result: RunResult,
        *,
        budget_state: Mapping[str, object] | None = None,
    ) -> None:
        suspended = list(
            (getattr(result, "final_state", {}) or {})
            .get("_rt_join", {})
            .get("suspended", [])
        )
        if not suspended:
            raise RuntimeError("maintenance workflow suspended without a resumable frontier")
        next_node_id, _mask, next_token_id, _parent_token_id = suspended[0]
        next_payload = dict(ctx.payload)
        if budget_state is not None:
            next_payload["maintenance_budget_state"] = _persisted_budget_state(budget_state)
        next_payload.update(
            {
                "continuation_run_id": str(getattr(result, "run_id", "")),
                "suspended_node_id": str(next_node_id),
                "suspended_token_id": str(next_token_id),
            }
        )
        self.engines.conversation.jobs.requeue_at_tail(ctx.job, payload=next_payload)
        self._emit_trace(
            "maintenance_job_requeued",
            workspace_id=str(getattr(ctx, "workspace_id", "")),
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=str(getattr(ctx, "request_node_id", "")),
            job_id=ctx.job_id,
            continuation_run_id=next_payload["continuation_run_id"],
            suspended_node_id=next_payload["suspended_node_id"],
            suspended_token_id=next_payload["suspended_token_id"],
        )

    def _acknowledge_job(self, ctx: MaintenanceJobExecutionContext) -> bool:
        """Acknowledge only the claim that produced this maintenance result."""
        acknowledged = self.engines.conversation.jobs.mark_done(
            ctx.job_id,
            claim_token=ctx.job.claim_token,
        )
        self._emit_trace(
            "maintenance_job_acknowledged" if acknowledged else "maintenance_stale_claim_rejected",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            maintenance_kind=ctx.maintenance_kind,
            claim_token_present=bool(ctx.job.claim_token),
        )
        return acknowledged

    def _emit_trace(self, event: str, **fields: object) -> None:
        if fields.get("job_id") is not None and not event.startswith("maintenance_lease_"):
            self._last_progress_monotonic = time.monotonic()
        payload: dict[str, object] = {
            "event": event,
            "worker_id": getattr(self, "worker_id", "maintenance-worker-unknown"),
            "thread_name": threading.current_thread().name,
            "ts_ms": int(time.time() * 1000),
            **fields,
        }
        logger.info("maintenance_trace %s", payload)
        sink = getattr(self, "trace_sink", None)
        if sink is not None:
            try:
                sink(payload)
            except Exception:
                logger.exception("Maintenance trace sink failed for event %s", event)

    def _emit_stale_claim_discarded(
        self,
        ctx: MaintenanceJobExecutionContext,
        *,
        reason: str,
    ) -> None:
        """Record a late attempt without replying, retrying, or acknowledging it."""
        self._emit_trace(
            "maintenance_repeated_work_discarded",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            maintenance_kind=ctx.maintenance_kind,
            reason=reason,
            authoritative_result=False,
        )

    def _assert_claim_owned(self, ctx: MaintenanceJobExecutionContext, *, reason: str) -> None:
        if self._claim_lost.is_set():
            self._emit_stale_claim_discarded(ctx, reason=reason)
            raise RuntimeError("maintenance claim is no longer owned")

    def _emit_lane_reply(
        self,
        *,
        workspace_id: str,
        source_document_id: str,
        request_node_id: str,
        reply_to_message_id: str | None,
        status: str,
        payload: dict[str, object],
    ) -> None:
        if not reply_to_message_id:
            return
        ns = WorkspaceNamespaces(workspace_id)
        msg_type = f"reply.maintenance.{status}"
        correlation_id = reply_to_message_id
        reply_idempotency_key = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance_reply",
                workspace_id,
                reply_to_message_id,
                msg_type,
                correlation_id,
            )
        )
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            lane_status = (
                status
                if status in {"completed", "failed", "cancelled", "suspended"}
                else "failed"
            )
            lane_error = payload if lane_status in {"failed", "cancelled"} else None
            lane_completed = lane_status in {"completed", "failed", "cancelled"}
            completed_replies = self.engines.conversation.read.get_nodes(
                where={
                    "$and": [
                        {"artifact_kind": "lane_message"},
                        {"reply_to_message_id": reply_to_message_id},
                        {"msg_type": "reply.maintenance.completed"},
                    ],
                },
                limit=1,
            )
            if completed_replies and lane_status != "completed":
                self._emit_trace(
                    "maintenance_lane_reply_suppressed",
                    workspace_id=workspace_id,
                    source_document_id=source_document_id,
                    request_node_id=request_node_id,
                    reply_to_message_id=reply_to_message_id,
                    attempted_status=lane_status,
                    winning_status="completed",
                )
                return
            reply_message_id: str | None = None
            existing_reply = self.engines.conversation.read.get_nodes(
                where={
                    "$and": [
                        {"artifact_kind": "lane_message"},
                        {"idempotency_key": reply_idempotency_key},
                    ],
                },
                limit=1,
            )
            if not existing_reply:
                existing_reply = self.engines.conversation.read.get_nodes(
                    where={
                        "$and": [
                            {"artifact_kind": "lane_message"},
                            {"reply_to_message_id": reply_to_message_id},
                            {"msg_type": msg_type},
                            {"correlation_id": correlation_id},
                        ],
                    },
                    limit=1,
                )
            if not existing_reply:
                sent_reply = self.engines.conversation.send_lane_message(
                    conversation_id=f"maintenance:{source_document_id or request_node_id}",
                    inbox_id="inbox:foreground",
                    sender_id="lane:worker:maintenance",
                    recipient_id="lane:foreground",
                    msg_type=msg_type,
                    purpose="maintenance",
                    payload={
                        "workspace_id": workspace_id,
                        "request_node_id": request_node_id,
                        **payload,
                    },
                    reply_to=reply_to_message_id,
                    correlation_id=correlation_id,
                    idempotency_key=reply_idempotency_key,
                )
                reply_message_id = str(sent_reply.message_id)
            else:
                reply_message_id = str(existing_reply[0].id)
            if reply_message_id:
                self.engines.conversation.update_lane_message_status(
                    message_id=reply_message_id,
                    status=lane_status,
                    error=lane_error,
                    completed=lane_completed,
                )
            self.engines.conversation.update_lane_message_status(
                message_id=reply_to_message_id,
                status=lane_status,
                error=lane_error,
                completed=lane_completed,
            )
            projected_update = getattr(
                self.engines.conversation.meta_sqlite,
                "update_projected_lane_message_status",
                None,
            )
            if callable(projected_update):
                projected_update(
                    message_id=reply_to_message_id,
                    status=lane_status,
                    error_json=(
                        json.dumps(lane_error, sort_keys=True, separators=(",", ":"))
                        if lane_error is not None
                        else None
                    ),
                )

        return RunSuccess(state_update=[])
