from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from kogwistar.runtime.analytics import summarize_execution_failure_patterns
from kogwistar.runtime.artifacts import write_versioned_artifact
from kogwistar.runtime.models import RunSuccess, StepRunResult
from kogwistar.runtime.resolvers import MappingStepResolver
from kogwistar.runtime.runtime import StepContext, WorkflowRuntime

from .models import NamespaceEngines, MaintenanceJobResult
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
        self.resolver.register("distill_from_history")(self.derive_problem_solving_wisdom_from_history)
        self.resolver.register("derive_problem_solving_wisdom_from_history")(self.derive_problem_solving_wisdom_from_history)
        self.resolver.register("check_done")(self._step_check_done)
        self.resolver.register("noop")(self._step_noop)

        def pred_continue(ctx):
            return True

        self.runtime = WorkflowRuntime(
            workflow_engine=self.engines.workflow,
            conversation_engine=self.engines.conversation,
            step_resolver=self.resolver,
            predicate_registry={
                "continue": pred_continue,
            },
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

        import warnings
        with _temporary_namespace(self.engines.conversation, ns.conv_bg), _temporary_namespace(
            self.engines.workflow, ns.workflow_maintenance
        ):
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=RuntimeWarning,
                    message="Using advanced underscore state key '_deps'",
                )
                try:
                    result = self.runtime.run(
                        workflow_id="maintenance.distillation.v1",
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
                    self._emit_execution_wisdom_from_history(workspace_id, self.engines)
                    logger.info(f"Maintenance job {req_node_id} execution finished: {status}")
                    if job_id:
                        self.engines.conversation.meta_sqlite.mark_index_job_done(job_id)
                except Exception as e:
                    logger.error(f"Maintenance job {req_node_id} encountered runtime error: {e}", exc_info=True)
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
                where={"artifact_kind": "promoted_knowledge"}
            )

        if not promoted_nodes:
            return RunSuccess(state_update=[("u", {"distillation_complete": True})])

        entity_groups: Dict[str, List[Any]] = {}
        for node in promoted_nodes:
            label = node.metadata.get("label") or getattr(node, "label", "Unknown Entity")
            entity_groups.setdefault(label, []).append(node)

        from kogwistar.engine_core.models import Grounding, Node, Span
        from kogwistar.id_provider import stable_id

        derived_engine = engines.derived_knowledge_engine()
        for label, nodes in entity_groups.items():
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
                merged_mentions = [Grounding(spans=[Span(
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
                )])]

            def _build_derived_node(existing: list[Any], created_at_ms: int) -> Node:
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

            write_versioned_artifact(
                derived_engine,
                namespace=ns.derived_knowledge,
                match_where={
                    "artifact_kind": "derived_knowledge",
                    "workspace_id": workspace_id,
                    "label": label,
                },
                build_node=_build_derived_node,
                replace_existing=True,
            )
            logger.info(
                f"Derived knowledge synthesis for entity '{label}' with {len(merged_mentions)} mentions."
            )

        return RunSuccess(
            state_update=[("u", {
                "distillation_complete": True,
                "derived_knowledge_complete": True,
                "distilled_entities": list(entity_groups.keys()),
            })]
        )

    def _step_check_done(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for checking if distillation is done."""
        should_continue = ctx.state_view.get("continue_distillation", False)
        if should_continue:
            return RunSuccess(state_update=[], next_step_names=["continue"])
        else:
            return RunSuccess(state_update=[], next_step_names=["finished"])

    def _emit_execution_wisdom_from_history(self, workspace_id: str, engines: NamespaceEngines) -> list[str]:
        """Analyze completed execution history and emit execution-derived wisdom."""
        min_failure_signals = 2

        if not workspace_id or not engines:
            return []

        ns = WorkspaceNamespaces(workspace_id)

        from kogwistar.engine_core.models import Grounding, Node, Span
        from kogwistar.id_provider import stable_id

        with _temporary_namespace(engines.conversation, ns.conv_bg):
            step_exec_nodes = engines.conversation.read.get_nodes(
                where={"entity_type": "workflow_step_exec"}
            )
        step_exec_nodes = [
            node for node in step_exec_nodes
            if node.metadata.get("status") in {"failure", "error"}
        ]

        if not step_exec_nodes:
            logger.debug("derive_problem_solving_wisdom_from_history: no failure records found - skipping")
            return []

        patterns = summarize_execution_failure_patterns(
            step_exec_nodes,
            min_failure_signals=min_failure_signals,
        )

        emitted: list[str] = []
        for pattern in patterns:
            step_op = pattern.step_op
            failure_nodes = list(pattern.failure_nodes)
            run_ids = list(pattern.run_ids)
            label = f"execution_failure_pattern:{step_op}"

            span = Span(
                collection_page_url=f"conversation/{ns.conv_bg}",
                document_page_url=f"conversation/{ns.conv_bg}",
                doc_id=f"conv:{ns.conv_bg}",
                insertion_method="execution_history",
                page_number=1,
                start_char=0,
                end_char=1,
                excerpt=f"failure_pattern:{step_op} n={len(failure_nodes)}",
                context_before="",
                context_after="",
                chunk_id=None,
                source_cluster_id=None,
            )

            def _build_wisdom_node(existing: list[Any], created_at_ms: int) -> Node:
                return Node(
                    id=str(stable_id("execution_wisdom", workspace_id, step_op, str(created_at_ms))),
                    label=label,
                    type="entity",
                    summary=(
                        f"Repeated failure pattern detected for workflow step '{step_op}' "
                        f"({len(failure_nodes)} occurrences across {len(run_ids)} runs). "
                        "Investigate step resolver, input contract, or upstream data quality."
                    ),
                    mentions=[Grounding(spans=[span])],
                    metadata={
                        "workspace_id": workspace_id,
                        "artifact_kind": "execution_wisdom",
                        "step_op": step_op,
                        "failure_count": len(failure_nodes),
                        "evidence_run_ids": run_ids,
                        "created_at_ms": created_at_ms,
                        "replaces_ids": [str(node.id) for node in existing],
                        "label": label,
                    },
                )

            write_versioned_artifact(
                engines.wisdom,
                namespace=ns.wisdom,
                match_where={
                    "artifact_kind": "execution_wisdom",
                    "workspace_id": workspace_id,
                    "step_op": step_op,
                },
                build_node=_build_wisdom_node,
                replace_existing=True,
            )
            emitted.append(step_op)
            logger.info(
                f"Emitted execution_wisdom for step_op='{step_op}' "
                f"(failures={len(failure_nodes)}, runs={len(run_ids)})"
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
