from __future__ import annotations

import logging
import json
from typing import Any, Optional
from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .projection import ProjectionManager
from .utils import _temporary_namespace

logger = logging.getLogger(__name__)

METASTATE_NAMESPACE = "obsidian_projection_state"


class ProjectionWorker:
    """
    Durable worker that ensures the Obsidian vault is in sync with the KG.
    Follows a strictly ordered linked-list of projection requests on the graph,
    but tracks its internal sequence state in the engine's metastore to avoid
    dummy/ungrounded nodes.
    """

    def __init__(self, engines: NamespaceEngines):
        self.engines = engines
        self.manager = ProjectionManager(engines)

    def process_pending_projections(self, workspace_id: str, vault_root: str):
        """Drains the projection queue in strict sequence order."""
        ns = WorkspaceNamespaces(workspace_id)
        
        while True:
            # 1. Get current sequence from internal metastore (Named Projections Table)
            current_seq = self._get_latest_projected_seq(workspace_id)
            next_seq = current_seq + 1
            
            # 2. Find the request with next_seq in the graph (Durable Request Log)
            # We fetch all and filter in-memory
            with _temporary_namespace(self.engines.conversation, ns.conv_bg):
                all_pending = self.engines.conversation.read.get_nodes(
                    where={
                        "workspace_id": workspace_id,
                        "artifact_kind": "projection_request",
                    }
                )
            
            # Find the specific node for next_seq
            next_reqs = [r for r in all_pending if int(r.metadata.get("seq", 0)) == next_seq]
            
            if not next_reqs:
                logger.debug(f"No candidate for seq {next_seq} found for workspace {workspace_id}")
                break
                
            req_node = next_reqs[0]
            self._handle_projection_request(workspace_id, req_node, vault_root)

    def _get_latest_projected_seq(self, workspace_id: str) -> int:
        """Retrieves the latest projected sequence from the meta_sqlite store."""
        # Aligning with kogwistar internal 'named_projections' pattern
        meta = self.engines.conversation.meta_sqlite
        key = f"ws:{workspace_id}"
        
        projection = meta.get_named_projection(METASTATE_NAMESPACE, key)
        if projection and isinstance(projection, dict):
            payload = projection.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if isinstance(payload, dict):
                return int(payload.get("latest_projected_seq", 0))
        return 0

    def _set_latest_projected_seq(self, workspace_id: str, seq: int):
        """Persists the latest projected sequence to the meta_sqlite store."""
        meta = self.engines.conversation.meta_sqlite
        key = f"ws:{workspace_id}"
        
        payload = {"latest_projected_seq": seq}
        # last_authoritative_seq and last_materialized_seq are used for consistency checks in core,
        # here we set them to simple values as our logic is driven by the application-level 'seq'.
        meta.replace_named_projection(
            namespace=METASTATE_NAMESPACE,
            key=key,
            payload=payload,
            last_authoritative_seq=seq,
            last_materialized_seq=seq,
            projection_schema_version=1,
            materialization_status="ready"
        )

    def _handle_projection_request(self, workspace_id: str, req_node: Any, vault_root: str):
        target_seq = int(req_node.metadata.get("seq", 0))
        logger.info(f"Processing projection request seq={target_seq} (ID: {req_node.id})")
        
        # 1. Update status to 'processing' on the graph node
        req_node.metadata["status"] = "processing"
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            self.engines.conversation.write.add_node(req_node)
        
        try:
            # 2. Perform the Sync via ProjectionManager
            # Reconciliation build: ensures Obsidian vault matches KG-visible state.
            self.manager.sync_obsidian_vault(
                vault_root=vault_root,
                workspace_id=workspace_id,
            )
            
            # 3. Update request status to 'completed'
            req_node.metadata["status"] = "completed"
            with _temporary_namespace(self.engines.conversation, ns.conv_bg):
                self.engines.conversation.write.add_node(req_node)
            
            # 4. Advance the sequence in the internal metastore
            self._set_latest_projected_seq(workspace_id, target_seq)
                
            logger.info(f"Successfully projected seq={target_seq}")
            
        except Exception as e:
            logger.error(f"Projection failed for seq={target_seq}: {e}")
            req_node.metadata["status"] = "failed"
            req_node.metadata["error"] = str(e)
            with _temporary_namespace(self.engines.conversation, ns.conv_bg):
                self.engines.conversation.write.add_node(req_node)
            raise # Strict Ordering: stop on failure
