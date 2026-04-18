from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from .models import NamespaceEngines, MaintenanceJobResult
from .namespaces import WorkspaceNamespaces

from .utils import _temporary_namespace
from kogwistar.runtime.runtime import WorkflowRuntime, StepContext
from kogwistar.runtime.resolvers import MappingStepResolver
from kogwistar.runtime.models import RunSuccess, StepRunResult


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
                "continue": pred_continue, # Default to always allowing the loop hint
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
        with _temporary_namespace(self.engines.conversation, ns.conv_bg), \
             _temporary_namespace(self.engines.workflow, ns.workflow_maintenance):
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
                        retry_count = int(getattr(job, "retry_count", None) or (job.get("retry_count") if isinstance(job, dict) else 0))
                        max_retries = int(getattr(job, "max_retries", None) or (job.get("max_retries") if isinstance(job, dict) else 10))
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
        
        The Distillation Algorithm:
        --------------------------
        1. Context Acquisition: Resolves the workspace engines and namespaces from the step context.
        2. Knowledge Discovery: Scans the KG namespace for all nodes marked as 'promoted_knowledge'.
        3. Canonical Grouping:
           - Iterates over discovered nodes and groups them by their canonical label (e.g., "Acme Corp").
           - Normalizes labels to ensure entities from different documents are merged correctly.
        4. Mentions Aggregation:
           - For each grouped entity, collects all `mentions` (Grounding + Spans) from every source node.
           - Ensures that the resulting derived artifact retains the full lineage of every document that mentioned it.
        5. Derived-Knowledge Materialization:
           - Constructs a `derived_knowledge` node with a stable ID.
           - Updates or creates the node in the derived-knowledge namespace.
           - Injects metadata linking back to the `source_node_ids` for traceability.
        """
        workspace_id = ctx.state_view.get("workspace_id")
        # _deps is passed directly as a NamespaceEngines object in _handle_request.
        # Support both: dict{"engines": ...} (resolver-docs pattern) and direct NamespaceEngines.
        _deps_raw = ctx.state_view.get("_deps")
        if isinstance(_deps_raw, dict):
            engines = _deps_raw.get("engines")
        else:
            engines = _deps_raw  # NamespaceEngines passed directly
        if not workspace_id or not engines:
            logger.error("Missing workspace_id or engines in distillation step context")
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])

        ns = WorkspaceNamespaces(workspace_id)
        
        # 1. Fetch all promoted knowledge for this workspace
        with _temporary_namespace(engines.kg, ns.kg):
            promoted_nodes = engines.kg.read.get_nodes(
                where={"artifact_kind": "promoted_knowledge"}
            )
        
        if not promoted_nodes:
            return RunSuccess(state_update=[("u", {"distillation_complete": True})])

        # 2. Group by label (Entity Name)
        entity_groups: Dict[str, List[Any]] = {}
        for node in promoted_nodes:
            # Prefer label in metadata (canonical entity name), fallback to node attribute
            label = node.metadata.get("label") or getattr(node, "label", "Unknown Entity")
            entity_groups.setdefault(label, []).append(node)

        # 3. Create derived-knowledge nodes with merged grounding
        from kogwistar.engine_core.models import Node
        from kogwistar.id_provider import stable_id
        
        derived_engine = engines.derived_knowledge_engine()
        with _temporary_namespace(derived_engine, ns.derived_knowledge):
            for label, nodes in entity_groups.items():
                # Merge all mentions from all occurrences of this entity
                raw_mentions = []
                for n in nodes:
                    if hasattr(n, "mentions") and n.mentions:
                        raw_mentions.extend(n.mentions)
                
                # Deduplicate mentions based on span identity
                # We use a set of serialized spans to detect duplicates
                merged_mentions = []
                seen_mentions = set()
                
                for m in raw_mentions:
                    # Create an identity key for the grounding based on its spans
                    # Since Grounding is a Pydantic model, we can use its JSON representation as a stable key
                    try:
                        m_key = m.model_dump_json()
                    except (AttributeError, Exception):
                        # Fallback for older models or unexpected structures
                        m_key = str(m)
                    
                    if m_key not in seen_mentions:
                        merged_mentions.append(m)
                        seen_mentions.add(m_key)
                
                if not merged_mentions:
                    from kogwistar.engine_core.models import Grounding, Span
                    ns = WorkspaceNamespaces(workspace_id)
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

                # Append-only: tombstone any existing wisdom node for this label,
                # then write a fresh version. This preserves the history chain
                # (tombstoned node is still discoverable) and avoids CRUD-style overwrite.
                import time as _time
                existing = derived_engine.read.get_nodes(
                    where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id, "label": label}
                )
                for old_node in existing:
                    try:
                        derived_engine.lifecycle.tombstone_node(str(old_node.id))
                    except Exception as e:
                        logger.warning(f"Could not tombstone old derived_knowledge node {old_node.id}: {e}")

                # New versioned ID — unique per distillation run
                version_ts = int(_time.time() * 1000)
                derived_node = Node(
                    id=str(stable_id("derived_knowledge", workspace_id, label, str(version_ts))),
                    label=label,
                    type="entity",
                    summary=f"Derived knowledge synthesis for {label} aggregated from {len(nodes)} source documents.",
                    mentions=merged_mentions,
                    metadata={
                        "workspace_id": workspace_id,
                        "artifact_kind": "derived_knowledge",
                        "source_node_ids": [str(n.id) for n in nodes],
                        "label": label,
                        "version_ts": version_ts,
                        "replaces_ids": [str(n.id) for n in existing],
                    }
                )

                # Keep derived knowledge in the knowledge engine, but under its own namespace.
                derived_engine.write.add_node(derived_node)
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
        """Analyze completed execution history and emit execution-derived wisdom.

        This is distinct from ``_step_distill`` (which aggregates *promoted knowledge*
        nodes). This step scans conversation-trace ``WorkflowRunNode`` and
        ``WorkflowStepExecNode`` records, identifies patterns in outcomes (failures,
        retries, latency outliers), and appends ``execution_wisdom`` nodes into the
        ``wisdom`` engine.

        Pattern extraction (current implementation):
        - Collect all step-level failure records for the workspace.
        - Group by ``step_op`` (the step resolver key that failed).
        - For each op with ≥ ``_MIN_FAILURE_SIGNALS`` failures, emit one
          ``execution_wisdom`` node describing the failure pattern.

        The node is append-only: any previous wisdom node for the same op pattern
        is tombstoned first.
        """
        _MIN_FAILURE_SIGNALS = 2  # only emit wisdom when we have repeated evidence

        if not workspace_id or not engines:
            return []

        ns = WorkspaceNamespaces(workspace_id)

        from kogwistar.engine_core.models import Grounding, Node, Span
        from kogwistar.id_provider import stable_id
        import time as _time

        # 1. Collect step failure records from the conversation trace lane.
        with _temporary_namespace(engines.conversation, ns.conv_bg):
            step_exec_nodes = engines.conversation.read.get_nodes(
                where={"entity_type": "workflow_step_exec"}
            )
        step_exec_nodes = [
            node for node in step_exec_nodes
            if node.metadata.get("status") in {"failure", "error"}
        ]

        if not step_exec_nodes:
            logger.debug("derive_problem_solving_wisdom_from_history: no failure records found — skipping")
            return []

        # 2. Group failures by step_op.
        from collections import defaultdict
        failures_by_op: dict[str, list[Any]] = defaultdict(list)
        for node in step_exec_nodes:
            step_op = node.metadata.get("step_op") or node.metadata.get("op") or node.metadata.get("wf_op") or "unknown"
            failures_by_op[step_op].append(node)

        # 3. Emit execution_wisdom nodes for ops with repeated failure evidence.
        emitted: list[str] = []
        with _temporary_namespace(engines.wisdom, ns.wisdom):
            for step_op, failure_nodes in failures_by_op.items():
                if len(failure_nodes) < _MIN_FAILURE_SIGNALS:
                    continue

                run_ids = sorted({n.metadata.get("run_id", "") for n in failure_nodes} - {""})
                label = f"execution_failure_pattern:{step_op}"

                # Tombstone any prior execution_wisdom node for this pattern.
                existing = engines.wisdom.read.get_nodes(
                    where={
                        "artifact_kind": "execution_wisdom",
                        "workspace_id": workspace_id,
                        "step_op": step_op,
                    }
                )
                for old in existing:
                    try:
                        engines.wisdom.lifecycle.tombstone_node(str(old.id))
                    except Exception as e:
                        logger.warning(f"Could not tombstone old execution_wisdom node {old.id}: {e}")

                version_ts = int(_time.time() * 1000)
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
                wisdom_node = Node(
                    id=str(stable_id("execution_wisdom", workspace_id, step_op, str(version_ts))),
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
                        "version_ts": version_ts,
                        "replaces_ids": [str(n.id) for n in existing],
                        "label": label,
                    },
                )
                engines.wisdom.write.add_node(wisdom_node)
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
