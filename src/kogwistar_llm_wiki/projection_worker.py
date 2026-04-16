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
            
            # 2. Find the request with next_seq directly via metadata filter
            with _temporary_namespace(self.engines.conversation, ns.conv_bg):
                next_reqs = self.engines.conversation.read.get_nodes(
                    where={
                        "workspace_id": workspace_id,
                        "artifact_kind": "projection_request",
                        "seq": next_seq,
                    }
                )
            
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
        ns = WorkspaceNamespaces(workspace_id)

        # Append-only: emit a status-event node rather than mutating the original request.
        # The original req_node stays immutable; status is tracked by discovery of these events.
        self._emit_projection_status(
            workspace_id=workspace_id,
            req_node_id=str(req_node.id),
            seq=target_seq,
            status="processing",
            ns=ns,
        )

        try:
            # Perform the Sync via ProjectionManager
            self.manager.sync_obsidian_vault(
                vault_root=vault_root,
                workspace_id=workspace_id,
            )

            self._emit_projection_status(
                workspace_id=workspace_id,
                req_node_id=str(req_node.id),
                seq=target_seq,
                status="completed",
                ns=ns,
            )

            # Advance the sequence in the internal metastore
            self._set_latest_projected_seq(workspace_id, target_seq)
            logger.info(f"Successfully projected seq={target_seq}")

        except Exception as e:
            logger.error(f"Projection failed for seq={target_seq}: {e}")
            self._emit_projection_status(
                workspace_id=workspace_id,
                req_node_id=str(req_node.id),
                seq=target_seq,
                status="failed",
                ns=ns,
                error=str(e),
            )
            raise  # Strict Ordering: stop on failure

    def _emit_projection_status(
        self,
        *,
        workspace_id: str,
        req_node_id: str,
        seq: int,
        status: str,
        ns: "WorkspaceNamespaces",
        error: str | None = None,
    ) -> None:
        """Append-only status event — never updates the original request node."""
        from kogwistar.engine_core.models import Node, Grounding, Span
        from kogwistar.id_provider import stable_id
        import time

        ts = int(time.time() * 1000)
        event_id = str(stable_id("projection_status", req_node_id, status, str(ts)))

        span = Span(
            collection_page_url=f"conversation/{ns.conv_bg}",
            document_page_url=f"conversation/{ns.conv_bg}",
            doc_id=f"conv:{ns.conv_bg}",
            insertion_method="system",
            page_number=1,
            start_char=0,
            end_char=1,
            excerpt=f"projection_status seq={seq} status={status}",
            context_before="",
            context_after="",
            chunk_id=None,
            source_cluster_id=None,
        )
        metadata: dict = {
            "workspace_id": workspace_id,
            "artifact_kind": "projection_status_event",
            "projection_request_id": req_node_id,
            "seq": seq,
            "status": status,
        }
        if error is not None:
            metadata["error"] = error

        event_node = Node(
            id=event_id,
            label=f"Projection Status: seq={seq} {status}",
            type="entity",
            summary=f"Projection request {req_node_id} transitioned to {status}",
            mentions=[Grounding(spans=[span])],
            metadata=metadata,
        )
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            self.engines.conversation.write.add_node(event_node)

