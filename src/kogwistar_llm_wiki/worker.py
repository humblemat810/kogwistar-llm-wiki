from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from kogwistar.runtime.models import RunSuccess, StepRunResult
from kogwistar.runtime.resolvers import MappingStepResolver
from kogwistar.runtime.runtime import StepContext, WorkflowRuntime
from kogwistar.maintenance.template import run_grouped_maintenance_template
from kogwistar.wisdom.template import write_execution_wisdom_artifacts

from .models import NamespaceEngines, MaintenanceJobResult
from .maintenance_policy import (
    DERIVED_KNOWLEDGE_WORKFLOW_ID,
    EXECUTION_WISDOM_WORKFLOW_ID,
    is_execution_wisdom_kind,
    workflow_id_for_maintenance_kind,
)
from .maintenance_designs import materialize_maintenance_designs
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace


logger = logging.getLogger(__name__)


class BaseWorker(ABC):
    """Base class for background workers polling the Kogwistar artifact stream."""

    def __init__(self, engines: NamespaceEngines):
        self.engines = engines

    def run_forever(self, workspace_id: str, interval: float = 5.0):
        """Main daemon loop."""
        logger.info(f"Starting worker loop for workspace {workspace_id}")
        while True:
            try:
                self.process_pending_jobs(workspace_id)
            except Exception as e:
                logger.error(f"Worker error in workspace {workspace_id}: {e}", exc_info=True)
            time.sleep(interval)

    @abstractmethod
    def process_pending_jobs(self, workspace_id: str):
        """Subclasses implement specific polling/processing logic."""
        pass


class MaintenanceWorker(BaseWorker):
    """
    Worker responsible for processing maintenance jobs using the durable job table
    and the graph-native runtime for the actual distillation work.
    """

    def __init__(self, engines: NamespaceEngines, eager_mode: bool = False):
        """
        Initialize the MaintenanceWorker.

        Args:
            engines: The namespace engines to use.
            eager_mode: If True, the worker may skip certain delays or provide hooks for immediate execution.
        """
        super().__init__(engines)
        self.eager_mode = eager_mode
        self.resolver = MappingStepResolver()
        self.resolver.register("distill")(self._step_distill)
        self.resolver.register("check_done")(self._step_check_done)
        self.resolver.register("distill_from_history")(self.derive_problem_solving_wisdom_from_history)
        self.resolver.register("derive_problem_solving_wisdom_from_history")(self.derive_problem_solving_wisdom_from_history)
        self.resolver.register("noop")(self._step_noop)

        self.runtime = WorkflowRuntime(
            workflow_engine=self.engines.workflow,
            conversation_engine=self.engines.conversation,
            step_resolver=self.resolver,
            predicate_registry={},
        )

    def process_pending_jobs(self, workspace_id: str):
        """
        Finds and processes maintenance jobs for a given workspace.
        The durable index job table is authoritative; graph nodes are retained only
        as audit artifacts.
        """
        ns = WorkspaceNamespaces(workspace_id)
        meta = self.engines.conversation.meta_sqlite
        while True:
            jobs = meta.claim_index_jobs(
                limit=50,
                lease_seconds=60,
                namespace=ns.maintenance_jobs,
            )
            if not jobs:
                break
            for job in jobs:
                self._handle_job(workspace_id, job)

    def _handle_job(self, workspace_id: str, job: Any):
        job_id = str(getattr(job, "job_id", None) or (job.get("job_id") if isinstance(job, dict) else ""))
        payload = self._decode_payload(job)
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
        ns = WorkspaceNamespaces(workspace_id)
        workflow_id = workflow_id_for_maintenance_kind(maintenance_kind)

        if is_execution_wisdom_kind(maintenance_kind):
            try:
                emitted = self._emit_execution_wisdom_from_history(workspace_id, self.engines)
                logger.info(
                    "Maintenance job %s execution finished: finished (%s emitted=%s)",
                    req_node_id,
                    workflow_id,
                    len(emitted),
                )
                self._emit_lane_reply(
                    workspace_id=workspace_id,
                    source_document_id=str(payload.get("source_document_id") or ""),
                    request_node_id=req_node_id,
                    reply_to_message_id=lane_message_id or None,
                    status="completed",
                    payload={
                        "maintenance_kind": maintenance_kind,
                        "execution_wisdom_emitted": emitted,
                    },
                )
                if job_id:
                    self.engines.conversation.meta_sqlite.mark_index_job_done(job_id)
            except Exception as e:
                logger.error(f"Maintenance job {req_node_id} encountered runtime error: {e}", exc_info=True)
                self._emit_lane_reply(
                    workspace_id=workspace_id,
                    source_document_id=str(payload.get("source_document_id") or ""),
                    request_node_id=req_node_id,
                    reply_to_message_id=lane_message_id or None,
                    status="failed",
                    payload={
                        "maintenance_kind": maintenance_kind,
                        "error": str(e),
                    },
                )
                if job_id:
                    retry_count = int(
                        getattr(job, "retry_count", None)
                        or (job.get("retry_count") if isinstance(job, dict) else 0)
                    )
                    max_retries = int(
                        getattr(job, "max_retries", None)
                        or (job.get("max_retries") if isinstance(job, dict) else 10)
                    )
                    if retry_count + 1 < max_retries:
                        self.engines.conversation.meta_sqlite.bump_retry_and_requeue(
                            job_id,
                            str(e),
                            next_run_at_seconds=min(300, 2 ** max(retry_count, 0)),
                        )
                    else:
                        self.engines.conversation.meta_sqlite.mark_index_job_failed(job_id, str(e), final=True)
            return

        import warnings
        with _temporary_namespace(self.engines.conversation, ns.conv_bg), _temporary_namespace(
            self.engines.workflow, ns.workflow_maintenance
        ):
            if not self.engines.workflow.read.get_nodes(
                where={
                    "$and": [
                        {"entity_type": "workflow_node"},
                        {"workflow_id": workflow_id},
                    ]
                },
                limit=1,
            ):
                materialize_maintenance_designs(self.engines.workflow)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=RuntimeWarning,
                    message="Using advanced underscore state key '_deps'",
                )
                try:
                    result = self.runtime.run(
                        workflow_id=workflow_id,
                        initial_state={
                            "workspace_id": workspace_id,
                            "request_id": req_node_id,
                            "maintenance_kind": maintenance_kind,
                            "_deps": self.engines,
                        },
                        conversation_id=ns.conv_bg,
                        turn_node_id=req_node_id,
                    )
                    status = result.status if hasattr(result, "status") else "finished"
                    logger.info(
                        "Maintenance job %s execution finished: %s (%s)",
                        req_node_id,
                        status,
                        workflow_id,
                    )
                    self._emit_lane_reply(
                        workspace_id=workspace_id,
                        source_document_id=str(payload.get("source_document_id") or ""),
                        request_node_id=req_node_id,
                        reply_to_message_id=lane_message_id or None,
                        status="completed",
                        payload={
                            "maintenance_kind": maintenance_kind,
                            "workflow_id": workflow_id,
                            "runtime_status": status,
                        },
                    )
                    if job_id:
                        self.engines.conversation.meta_sqlite.mark_index_job_done(job_id)
                except Exception as e:
                    logger.error(f"Maintenance job {req_node_id} encountered runtime error: {e}", exc_info=True)
                    self._emit_lane_reply(
                        workspace_id=workspace_id,
                        source_document_id=str(payload.get("source_document_id") or ""),
                        request_node_id=req_node_id,
                        reply_to_message_id=lane_message_id or None,
                        status="failed",
                        payload={
                            "maintenance_kind": maintenance_kind,
                            "workflow_id": workflow_id,
                            "error": str(e),
                        },
                    )
                    if job_id:
                        retry_count = int(
                            getattr(job, "retry_count", None)
                            or (job.get("retry_count") if isinstance(job, dict) else 0)
                        )
                        max_retries = int(
                            getattr(job, "max_retries", None)
                            or (job.get("max_retries") if isinstance(job, dict) else 10)
                        )
                        if retry_count + 1 < max_retries:
                            self.engines.conversation.meta_sqlite.bump_retry_and_requeue(
                                job_id,
                                str(e),
                                next_run_at_seconds=min(300, 2 ** max(retry_count, 0)),
                            )
                        else:
                            self.engines.conversation.meta_sqlite.mark_index_job_failed(job_id, str(e), final=True)

    def _emit_lane_reply(
        self,
        *,
        workspace_id: str,
        source_document_id: str,
        request_node_id: str,
        reply_to_message_id: str | None,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        if not reply_to_message_id:
            return
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            self.engines.conversation.send_lane_message(
                conversation_id=f"maintenance:{source_document_id or request_node_id}",
                inbox_id="inbox:foreground",
                sender_id="lane:worker:maintenance",
                recipient_id="lane:foreground",
                msg_type=f"reply.maintenance.{status}",
                payload={
                    "workspace_id": workspace_id,
                    "request_node_id": request_node_id,
                    **payload,
                },
                reply_to=reply_to_message_id,
                correlation_id=reply_to_message_id,
            )
            self.engines.conversation.update_lane_message_status(
                message_id=reply_to_message_id,
                status="completed" if status == "completed" else "failed",
                error=(payload if status != "completed" else None),
                completed=True,
            )

    def _decode_payload(self, job: Any) -> dict[str, Any]:
        payload = getattr(job, "payload_json", None)
        if payload is None and isinstance(job, dict):
            payload = job.get("payload_json")
        if isinstance(payload, str) and payload:
            try:
                decoded = json.loads(payload)
                if isinstance(decoded, dict):
                    return decoded
            except Exception:
                pass
        return {}

    def _load_request_node(self, workspace_id: str, req_node_id: str) -> Any | None:
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            nodes = self.engines.conversation.read.get_nodes(
                where={
                    "workspace_id": workspace_id,
                    "id": req_node_id,
                }
            )
        if nodes:
            return nodes[0]
        return None

    def _step_distill(self, ctx: StepContext) -> StepRunResult:
        """
        Resolver step for distillation: aggregates promoted knowledge into
        derived-knowledge artifacts.
        """
        workspace_id = ctx.state_view.get("workspace_id")
        _deps_raw = ctx.state_view.get("_deps")
        if isinstance(_deps_raw, dict):
            engines = _deps_raw.get("engines")
        else:
            engines = _deps_raw
        if not workspace_id or not engines:
            logger.error("Missing workspace_id or engines in distillation step context")
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])

        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(engines.kg, ns.kg):
            promoted_nodes = engines.kg.read.get_nodes(
                where={"artifact_kind": "promoted_knowledge", "workspace_id": workspace_id}
            )

        if not promoted_nodes:
            return RunSuccess(state_update=[("u", {"distillation_complete": True})])

        from kogwistar.engine_core.models import Grounding, Node, Span
        from kogwistar.id_provider import stable_id

        derived_engine = engines.derived_knowledge_engine()
        template_result = run_grouped_maintenance_template(
            engines.kg,
            target_engine=derived_engine,
            source_namespace=ns.kg,
            target_namespace=ns.derived_knowledge,
            source_where={"artifact_kind": "promoted_knowledge", "workspace_id": workspace_id},
            group_key_for_node=lambda node: str(node.metadata.get("label") or getattr(node, "label", "Unknown Entity")),
            match_where_for_group=lambda label: {
                "artifact_kind": "derived_knowledge",
                "workspace_id": workspace_id,
                "label": label,
            },
            build_node_for_group=lambda label, nodes, existing, created_at_ms: self._build_derived_node_for_group(
                workspace_id=workspace_id,
                label=label,
                nodes=nodes,
                existing=existing,
                created_at_ms=created_at_ms,
                fallback_span_factory=lambda: Grounding(spans=[Span(
                    collection_page_url=f"conversation/{ns.conv_bg}",
                    document_page_url=f"conversation/{ns.conv_bg}",
                    doc_id=f"conv:{ns.conv_bg}",
                    insertion_method="workflow_trace",
                    page_number=1,
                    start_char=0,
                    end_char=1,
                    excerpt=f"distilled:{label}",
                    context_before="",
                    context_after="",
                    chunk_id=None,
                    source_cluster_id=None,
                )]),
            ),
        )
        for result in template_result.grouped_results:
            logger.info(
                "Derived knowledge synthesis for entity '%s' with %s source nodes.",
                result.group_key,
                result.source_node_count,
            )

        return RunSuccess(
            state_update=[("u", {
                "distillation_complete": True,
                "derived_knowledge_complete": True,
                "distilled_entities": list(template_result.emitted_group_keys),
            })]
        )

    def _step_check_done(self, ctx: StepContext) -> StepRunResult:
        """Resolver step that cleanly finalizes derived-knowledge maintenance."""
        workspace_id = ctx.state_view.get("workspace_id")
        _deps_raw = ctx.state_view.get("_deps")
        engines = _deps_raw.get("engines") if isinstance(_deps_raw, dict) else _deps_raw
        if not workspace_id or not engines:
            logger.error("Missing workspace_id or engines in maintenance completion step context")
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])

        return RunSuccess(
            state_update=[("u", {
                "maintenance_complete": True,
            })]
        )

    def _build_derived_node_for_group(
        self,
        *,
        workspace_id: str,
        label: str,
        nodes: list[Any],
        existing: list[Any],
        created_at_ms: int,
        fallback_span_factory,
    ):
        from kogwistar.engine_core.models import Grounding, Node
        from kogwistar.id_provider import stable_id

        raw_mentions = []
        for node in nodes:
            if hasattr(node, "mentions") and node.mentions:
                raw_mentions.extend(node.mentions)

        merged_mentions = []
        seen_mentions = set()
        for mention in raw_mentions:
            try:
                mention_key = mention.model_dump_json()
            except (AttributeError, Exception):
                mention_key = str(mention)

            if mention_key not in seen_mentions:
                merged_mentions.append(mention)
                seen_mentions.add(mention_key)

        if not merged_mentions:
            merged_mentions = [fallback_span_factory()]

        return Node(
            id=str(stable_id("derived_knowledge", workspace_id, label, str(created_at_ms))),
            label=label,
            type="entity",
            summary=f"Derived knowledge synthesis for {label} aggregated from {len(nodes)} source documents.",
            mentions=merged_mentions,
            metadata={
                "workspace_id": workspace_id,
                "artifact_kind": "derived_knowledge",
                "source_node_ids": [str(node.id) for node in nodes],
                "label": label,
                "created_at_ms": created_at_ms,
                "replaces_ids": [str(node.id) for node in existing],
            },
        )

    def _emit_execution_wisdom_from_history(self, workspace_id: str, engines: NamespaceEngines) -> list[str]:
        """Analyze completed execution history and emit execution-derived wisdom."""
        if not workspace_id or not engines:
            return []

        ns = WorkspaceNamespaces(workspace_id)
        from kogwistar.engine_core.models import Grounding, Node, Span
        from kogwistar.id_provider import stable_id

        result_items = write_execution_wisdom_artifacts(
            engines.conversation,
            target_engine=engines.wisdom,
            source_namespace=ns.conv_bg,
            target_namespace=ns.wisdom,
            source_where={"entity_type": "workflow_step_exec"},
            min_failure_signals=2,
            match_where_for_pattern=lambda pattern: {
                "artifact_kind": "execution_wisdom",
                "workspace_id": workspace_id,
                "step_op": pattern.step_op,
            },
            build_node_for_pattern=lambda pattern, existing, created_at_ms: Node(
                id=str(stable_id("execution_wisdom", workspace_id, pattern.step_op, str(created_at_ms))),
                label=f"execution_failure_pattern:{pattern.step_op}",
                type="entity",
                summary=(
                    f"Repeated failure pattern detected for workflow step '{pattern.step_op}' "
                    f"({len(pattern.failure_nodes)} occurrences across {len(pattern.run_ids)} runs). "
                    "Investigate step resolver, input contract, or upstream data quality."
                ),
                mentions=[Grounding(spans=[Span(
                    collection_page_url=f"conversation/{ns.conv_bg}",
                    document_page_url=f"conversation/{ns.conv_bg}",
                    doc_id=f"conv:{ns.conv_bg}",
                    insertion_method="execution_history",
                    page_number=1,
                    start_char=0,
                    end_char=1,
                    excerpt=f"failure_pattern:{pattern.step_op} n={len(pattern.failure_nodes)}",
                    context_before="",
                    context_after="",
                    chunk_id=None,
                    source_cluster_id=None,
                )])],
                metadata={
                    "workspace_id": workspace_id,
                    "artifact_kind": "execution_wisdom",
                    "step_op": pattern.step_op,
                    "failure_count": len(pattern.failure_nodes),
                    "evidence_run_ids": list(pattern.run_ids),
                    "created_at_ms": created_at_ms,
                    "replaces_ids": [str(node.id) for node in existing],
                    "label": f"execution_failure_pattern:{pattern.step_op}",
                },
            ),
        )

        emitted = [result.step_op for result in result_items]
        for result in result_items:
            logger.info(
                f"Emitted execution_wisdom for step_op='{result.step_op}' "
                f"(failures={result.failure_count}, runs={len(result.run_ids)})"
            )

        return emitted

    def derive_problem_solving_wisdom_from_history(self, ctx: StepContext) -> StepRunResult:
        """Resolver wrapper for workflow-native execution-wisdom extraction."""
        workspace_id = ctx.state_view.get("workspace_id")
        _deps_raw = ctx.state_view.get("_deps")
        engines = _deps_raw.get("engines") if isinstance(_deps_raw, dict) else _deps_raw
        emitted = self._emit_execution_wisdom_from_history(workspace_id, engines)
        return RunSuccess(
            state_update=[("u", {
                "history_wisdom_complete": True,
                "execution_wisdom_emitted": emitted,
            })]
        )

    def _step_distill_from_history(self, ctx: StepContext) -> StepRunResult:
        """Compatibility alias for older workflow step names."""
        return self.derive_problem_solving_wisdom_from_history(ctx)

    def _step_noop(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for terminal/noop nodes."""
        return RunSuccess(state_update=[])
