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

    def __init__(self, engines: NamespaceEngines):
        super().__init__(engines)
        self.resolver = MappingStepResolver()
        self.resolver.register("distill")(self._step_distill)
        self.resolver.register("check_done")(self._step_check_done)
        self.resolver.register("noop")(self._step_noop)
        
        self.runtime = WorkflowRuntime(
            workflow_engine=self.engines.workflow,
            conversation_engine=self.engines.conversation,
            step_resolver=self.resolver,
            predicate_registry={
                "continue": lambda ctx: True, # Default to always allowing the loop hint
            },
        )

    def process_pending_jobs(self, workspace_id: str):
        ns = WorkspaceNamespaces(workspace_id)
        # 1. Discover backbone run for this worker instance (or create one)
        backbone_run_id = self._ensure_backbone_run(workspace_id)

        # 2. Find pending maintenance requests in the conversation engine (conv_bg)
        requests = self.engines.conversation.read.get_nodes(
            where={
                "workspace_id": workspace_id,
                "artifact_kind": "maintenance_job_request",
                "namespace": ns.conv_bg,
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
            self.engines.conversation.backend.node_update(
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
            
            # 3. Mark request as completed
            status = "completed" if run_result.status == "succeeded" else "failed"
            self.engines.conversation.backend.node_update(
                ids=[req_node.id],
                metadatas=[{"status": status, "run_id": run_result.run_id, "namespace": ns.conv_bg}]
            )
            
            logger.info(f"Maintenance job {req_node.id} finished with status: {status}")

    def _step_distill(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for distillation."""
        logger.info(f"Executing distilled step in runtime for {ctx.run_id}")
        # Logic here would extract wisdom...
        # We can read context_window_size from state
        window_size = ctx.state_view.get("context_window_size", 4000)
        logger.info(f"Using context window size: {window_size}")
        
        return RunSuccess(
            state_update=[("u", {"distillation_complete": True})]
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
