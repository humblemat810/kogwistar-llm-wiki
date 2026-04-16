from __future__ import annotations

import json
import logging
from typing import Any
from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .projection import ProjectionManager
from .utils import _temporary_namespace

logger = logging.getLogger(__name__)

class ProjectionWorker:
    """
    Durable worker that ensures the Obsidian vault is in sync with the KG.
    The job table in the conversation metastore is authoritative; the graph
    only keeps audit/status nodes for traceability.
    """

    def __init__(self, engines: NamespaceEngines):
        self.engines = engines
        self.manager = ProjectionManager(engines)

    def process_pending_projections(self, workspace_id: str, vault_root: str):
        """Drains the projection job queue in durable claim order."""
        ns = WorkspaceNamespaces(workspace_id)
        meta = self.engines.conversation.meta_sqlite
        while True:
            jobs = meta.claim_index_jobs(
                limit=50,
                lease_seconds=60,
                namespace=ns.projection_jobs,
            )
            if not jobs:
                logger.debug("No projection jobs found for workspace %s", workspace_id)
                break
            for job in jobs:
                self._handle_projection_job(workspace_id, job, vault_root)

    def _handle_projection_job(self, workspace_id: str, job: Any, vault_root: str):
        job_id = str(getattr(job, "job_id", None) or (job.get("job_id") if isinstance(job, dict) else ""))
        entity_id = str(getattr(job, "entity_id", None) or (job.get("entity_id") if isinstance(job, dict) else ""))
        payload = self._decode_payload(job)
        promoted_entity_id = str(payload.get("promoted_entity_id") or entity_id)
        ns = WorkspaceNamespaces(workspace_id)

        logger.info("Processing projection job %s for entity %s", job_id, promoted_entity_id)
        self._record_projection_manifest(
            workspace_id=workspace_id,
            promoted_entity_id=promoted_entity_id,
            status="rebuilding",
        )
        self._emit_projection_status(
            workspace_id=workspace_id,
            req_node_id=job_id,
            promoted_entity_id=promoted_entity_id,
            status="processing",
            ns=ns,
        )

        try:
            self.manager.sync_obsidian_vault(
                vault_root=vault_root,
                workspace_id=workspace_id,
            )
            self._record_projection_manifest(
                workspace_id=workspace_id,
                promoted_entity_id=promoted_entity_id,
                status="ready",
            )
            self._emit_projection_status(
                workspace_id=workspace_id,
                req_node_id=job_id,
                promoted_entity_id=promoted_entity_id,
                status="completed",
                ns=ns,
            )
            if job_id:
                self.engines.conversation.meta_sqlite.mark_index_job_done(job_id)
            logger.info("Successfully projected entity %s", promoted_entity_id)
        except Exception as e:
            logger.error("Projection failed for job %s: %s", job_id, e)
            self._record_projection_manifest(
                workspace_id=workspace_id,
                promoted_entity_id=promoted_entity_id,
                status="rebuilding",
            )
            self._emit_projection_status(
                workspace_id=workspace_id,
                req_node_id=job_id,
                promoted_entity_id=promoted_entity_id,
                status="failed",
                ns=ns,
                error=str(e),
            )
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
            raise

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

    def _record_projection_manifest(
        self,
        *,
        workspace_id: str,
        promoted_entity_id: str,
        status: str,
    ) -> None:
        meta = self.engines.conversation.meta_sqlite
        ns = WorkspaceNamespaces(workspace_id)
        row = meta.get_named_projection(ns.projection_manifest, workspace_id) or {}
        payload = row.get("payload") if isinstance(row, dict) else {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

        projected_ids = [str(item) for item in payload.get("projected_ids", []) if str(item)]
        if promoted_entity_id not in projected_ids:
            projected_ids.append(promoted_entity_id)

        version = int(payload.get("projection_schema_version", 1) or 1)
        count = len(projected_ids)
        meta.replace_named_projection(
            namespace=ns.projection_manifest,
            key=workspace_id,
            payload={
                "workspace_id": workspace_id,
                "projected_ids": projected_ids,
                "status": status,
            },
            last_authoritative_seq=count,
            last_materialized_seq=count if status == "ready" else max(0, count - 1),
            projection_schema_version=version,
            materialization_status=status,
        )

    def _emit_projection_status(
        self,
        *,
        workspace_id: str,
        req_node_id: str,
        status: str,
        ns: "WorkspaceNamespaces",
        promoted_entity_id: str,
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
            excerpt=f"projection_status entity={promoted_entity_id} status={status}",
            context_before="",
            context_after="",
            chunk_id=None,
            source_cluster_id=None,
        )
        metadata: dict = {
            "workspace_id": workspace_id,
            "artifact_kind": "projection_status_event",
            "projection_request_id": req_node_id,
            "promoted_entity_id": promoted_entity_id,
            "status": status,
        }
        if error is not None:
            metadata["error"] = error

        event_node = Node(
            id=event_id,
            label=f"Projection Status: {promoted_entity_id} {status}",
            type="entity",
            summary=f"Projection request {req_node_id} transitioned to {status}",
            mentions=[Grounding(spans=[span])],
            metadata=metadata,
        )
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            self.engines.conversation.write.add_node(event_node)
