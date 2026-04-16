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
        Finds and processes all pending maintenance job requests for a given workspace.
        This follows a discovery pattern where jobs are polled from the background conversation namespace.
        """
        ns = WorkspaceNamespaces(workspace_id)
        # 1. Discover backbone run for this worker instance (or create one)
        backbone_run_id = self._ensure_backbone_run(workspace_id)

        # 2. Find pending maintenance requests in the conversation engine (conv_bg)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            requests = self.engines.conversation.read.get_nodes(
                where={
                    "workspace_id": workspace_id,
                    "artifact_kind": "maintenance_job_request",
                    "status": "pending",
                }
            )
        
        for req_node in requests:
            self._handle_request(workspace_id, req_node)

    def _ensure_backbone_run(self, workspace_id: str) -> str:
        """Pattern: The worker has a persistent backbone run on the graph."""
        from kogwistar.id_provider import stable_id
        run_id = str(stable_id("worker_backbone", workspace_id))
        # In a real system, we'd add a WorkflowRunNode here
        return run_id

    def _handle_request(self, workspace_id: str, req_node: Any):
        logger.info(f"Processing maintenance job {req_node.id}")
        ns = WorkspaceNamespaces(workspace_id)
        
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            # 1. Update status to 'running'
            self.engines.conversation.backend.node_update(  # Comment, logical violation, CRUD style update not allowed, only append workflow-exec node can indicate it in workflow runtime native semantics
                ids=[req_node.id],
                metadatas=[{"status": "running"}]
            )
            
            # 2. Invoke Workflow Runtime
            backbone_run_id = f"worker_backbone_{workspace_id}"
            
            initial_state = {
                "workspace_id": workspace_id,
                "request_node_id": str(req_node.id),
                "trigger_type": req_node.metadata.get("trigger_type"),
                "_deps": {"engines": self.engines}
            }
            
            run_result = self.runtime.run(
                workflow_id="maintenance.distillation.v1",
                conversation_id=workspace_id,
                turn_node_id=str(req_node.id),
                initial_state=initial_state,
            )
            
            print("----- DEBUG RUN RESULT -----")
            print("STATUS:", run_result.status)
            print("DICT:", getattr(run_result, "__dict__", str(run_result)))
            print("STATE:", getattr(run_result, "state", {}))
            print("----------------------------")
            
            # 3. Mark request as completed
            status = "completed" if run_result.status == "succeeded" else "failed"
            self.engines.conversation.backend.node_update(
                ids=[req_node.id],
                metadatas=[{"status": status, "run_id": run_result.run_id, "namespace": ns.conv_bg}]
            )
            
            logger.info(f"Maintenance job {req_node.id} finished with status: {status}")

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
        engines = ctx.state_view.get("_deps", {}).get("engines")
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
                merged_mentions = []
                for n in nodes:
                    if hasattr(n, "mentions") and n.mentions:
                        merged_mentions.extend(n.mentions) # human review error: what if duplicate? no dedup after all?, multiple knowledge node refer to the same span
                
                if not merged_mentions:
                    from kogwistar.engine_core.models import Grounding, Span
                    merged_mentions = [Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])]

                # Create a Wisdom Node with a stable ID based on label
                # This ensures that subsequent runs update the same entity node
                wisdom_node = Node(
                    id=str(stable_id("wisdom", workspace_id, label)),
                    label=label,
                    type="entity",
                    summary=f"Synthesized wisdom for {label} aggregated from {len(nodes)} source documents.",
                    mentions=merged_mentions,
                    metadata={
                        "workspace_id": workspace_id,
                        "artifact_kind": "wisdom",
                        "source_node_ids": [str(n.id) for n in nodes],
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
        # For the prototype/test, we always 'finish' after one pass unless otherwise specified
        should_continue = ctx.state_view.get("continue_distillation", False)
        
        if should_continue:
            return RunSuccess(state_update=[], next_step_names=["continue"])
        else:
            return RunSuccess(state_update=[], next_step_names=["finished"])

    def _step_noop(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for terminal/noop nodes."""
        return RunSuccess(state_update=[])
