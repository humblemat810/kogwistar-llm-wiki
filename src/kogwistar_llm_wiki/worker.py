from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

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
    Worker responsible for processing maintenance job requests using the graph-native runtime.
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
        Finds and processes maintenance job requests for a given workspace.
        This follows a discovery pattern where jobs are polled from the background conversation namespace.
        Authoritative Source: maintenance_job_request nodes in conv_bg.
        Deduplication Strategy: Checks for existing WorkflowStepExecNode traces.
        """
        ns = WorkspaceNamespaces(workspace_id)
        # 1. Discover backbone run for this worker instance (or create one)
        backbone_run_id = self._ensure_backbone_run(workspace_id)

        # 2. Find maintenance requests in the conversation engine (conv_bg)
        # Filter out those that have already been executed.
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            requests = self.engines.conversation.read.get_nodes(
                where={
                    "workspace_id": workspace_id,
                    "artifact_kind": "maintenance_job_request",
                }
            )

            for req_node in requests:
                # 1. Find all workflow run attempts for this request (bare keys, no metadata. prefix)
                run_nodes = self.engines.conversation.read.get_nodes(
                    where={
                        "turn_node_id": str(req_node.id),
                        "entity_type": "workflow_run",
                    }
                )

                # 2. Authoritative check: Does any run have a corresponding completion event?
                is_done = False
                for rn in run_nodes:
                    run_id = rn.metadata.get("run_id")
                    if not run_id:
                        continue
                    completions = self.engines.conversation.read.get_nodes(
                        where={
                            "entity_type": "workflow_completed",
                            "run_id": str(run_id),
                        }
                    )
                    if completions:
                        is_done = True
                        break

                if not is_done:
                    logger.info(f"Found pending maintenance request {req_node.id}, starting distillation.")
                    self._handle_request(workspace_id, req_node)
                else:
                    logger.debug(f"Maintenance request {req_node.id} already processed (completion trace found).")

    def _ensure_backbone_run(self, workspace_id: str) -> str:
        """Pattern: The worker has a persistent backbone run on the graph."""
        from kogwistar.id_provider import stable_id
        run_id = str(stable_id("worker_backbone", workspace_id))
        return run_id

    def _handle_request(self, workspace_id: str, req_node: Any):
        logger.info(f"Processing maintenance job {req_node.id}")
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
                            "request_id": str(req_node.id),
                            "maintenance_kind": req_node.metadata.get("maintenance_kind", "distill"),
                            "_deps": self.engines,
                        },
                        conversation_id=ns.conv_bg,
                        turn_node_id=str(req_node.id),
                    )
                    status = result.status if hasattr(result, "status") else "finished"
                    logger.info(f"Maintenance job {req_node.id} execution finished: {status}")
                except Exception as e:
                    logger.error(f"Maintenance job {req_node.id} encountered runtime error: {e}", exc_info=True)

    def _step_distill(self, ctx: StepContext) -> StepRunResult:
        """
        Resolver step for distillation: Aggregates promoted knowledge into the wisdom namespace.
        
        The Distillation Algorithm:
        --------------------------
        1. Context Acquisition: Resolves the workspace engines and namespaces from the step context.
        2. Knowledge Discovery: Scans the KG namespace for all nodes marked as 'promoted_knowledge'.
        3. Canonical Grouping:
           - Iterates over discovered nodes and groups them by their canonical label (e.g., "Acme Corp").
           - Normalizes labels to ensure entities from different documents are merged correctly.
        4. Mentions Aggregation:
           - For each grouped entity, collects all `mentions` (Grounding + Spans) from every source node.
           - Ensures that the resulting 'wisdom' node retains the full lineage of every document that mentioned it.
        5. Wisdom Materialization:
           - Constructs a `wisdom` node with a stable ID (derived from workspace and label).
           - Updates or creates the node in the `wisdom` namespace.
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

        # 3. Create Wisdom Nodes with Merged Grounding
        from kogwistar.engine_core.models import Node
        from kogwistar.id_provider import stable_id
        
        with _temporary_namespace(engines.wisdom, ns.wisdom):
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
                existing = engines.wisdom.read.get_nodes(
                    where={"artifact_kind": "wisdom", "workspace_id": workspace_id, "label": label}
                )
                for old_node in existing:
                    try:
                        engines.wisdom.lifecycle.tombstone_node(str(old_node.id))
                    except Exception as e:
                        logger.warning(f"Could not tombstone old wisdom node {old_node.id}: {e}")

                # New versioned ID — unique per distillation run
                version_ts = int(_time.time() * 1000)
                wisdom_node = Node(
                    id=str(stable_id("wisdom", workspace_id, label, str(version_ts))),
                    label=label,
                    type="entity",
                    summary=f"Synthesized wisdom for {label} aggregated from {len(nodes)} source documents.",
                    mentions=merged_mentions,
                    metadata={
                        "workspace_id": workspace_id,
                        "artifact_kind": "wisdom",
                        "source_node_ids": [str(n.id) for n in nodes],
                        "label": label,
                        "version_ts": version_ts,
                        "replaces_ids": [str(n.id) for n in existing],
                    }
                )

                # Add to wisdom engine
                engines.wisdom.write.add_node(wisdom_node)
                logger.info(f"Distilled wisdom for entity '{label}' with {len(merged_mentions)} mentions.")

        return RunSuccess(
            state_update=[("u", {"distillation_complete": True, "distilled_entities": list(entity_groups.keys())})]
        )

    def _step_check_done(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for checking if distillation is done."""
        should_continue = ctx.state_view.get("continue_distillation", False)
        if should_continue:
            return RunSuccess(state_update=[], next_step_names=["continue"])
        else:
            return RunSuccess(state_update=[], next_step_names=["finished"])

    def _step_distill_from_history(self, ctx: StepContext) -> StepRunResult:
        """Resolver step: derive wisdom from workflow execution outcomes.

        This is distinct from ``_step_distill`` (which aggregates *promoted knowledge*
        nodes).  This step scans the *workflow engine* for ``WorkflowRunNode`` and
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

        workspace_id = ctx.state_view.get("workspace_id")
        _deps_raw = ctx.state_view.get("_deps")
        engines = _deps_raw.get("engines") if isinstance(_deps_raw, dict) else _deps_raw
        if not workspace_id or not engines:
            return RunSuccess(state_update=[("u", {"history_wisdom_complete": True})])

        ns = WorkspaceNamespaces(workspace_id)

        from kogwistar.engine_core.models import Grounding, Node, Span
        from kogwistar.id_provider import stable_id
        import time as _time

        # 1. Collect step failure records from the workflow engine.
        with _temporary_namespace(engines.workflow, ns.workflow_maintenance):
            step_exec_nodes = engines.workflow.read.get_nodes(
                where={"entity_type": "workflow_step_exec", "status": "failure"}
            )

        if not step_exec_nodes:
            logger.debug("_step_distill_from_history: no failure records found — skipping")
            return RunSuccess(state_update=[("u", {"history_wisdom_complete": True})])

        # 2. Group failures by step_op.
        from collections import defaultdict
        failures_by_op: dict[str, list[Any]] = defaultdict(list)
        for node in step_exec_nodes:
            step_op = node.metadata.get("step_op") or node.metadata.get("wf_op") or "unknown"
            if node.metadata.get("workspace_id") == workspace_id:
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
                    collection_page_url=f"workflow/{ns.workflow_maintenance}",
                    document_page_url=f"workflow/{ns.workflow_maintenance}",
                    doc_id=f"wf:{ns.workflow_maintenance}",
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

        return RunSuccess(
            state_update=[("u", {
                "history_wisdom_complete": True,
                "execution_wisdom_emitted": emitted,
            })]
        )

    def _step_noop(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for terminal/noop nodes."""
        return RunSuccess(state_update=[])
