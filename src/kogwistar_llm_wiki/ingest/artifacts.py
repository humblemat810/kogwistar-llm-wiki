"""Shared artifact and revision helpers used by ingestion mixins."""

from __future__ import annotations

from collections.abc import Mapping

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.models import Node, Span

from ..maintenance.maintenance_guards import SourceRevision
from ..models import IngestPipelineRequest
from ..utils import _temporary_namespace


class IngestArtifactSupportMixin:
    """Build grounded artifact nodes and resolve durable identity helpers."""

    def _artifact_node(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
        node_id: str | None = None,
        artifact_kind: str,
        lane: str,
        visibility: str,
        label: str,
        summary: str,
        extra_metadata: dict[str, object] | None = None,
    ) -> Node:
        span = self._leading_span(source_document_id, request.raw_text, insertion_method=artifact_kind)
        extra_meta = dict(extra_metadata or {})
        normalized_visibility = self.policies.visibility.visibility_for(
            {
                "artifact_kind": artifact_kind,
                "visibility": visibility,
                "projection_visible": extra_meta.get("projection_visible"),
            }
        )
        metadata = {
            "workspace_id": request.workspace_id,
            "source_document_id": source_document_id,
            "source_uri": request.source_uri,
            "artifact_kind": artifact_kind,
            "namespace": namespace,
            "conversation_lane": lane,
            "visibility": normalized_visibility,
            "title": request.title,
            "parser_mode": request.parser_mode,
            "requires_provenance": self.policies.lifecycle.requires_provenance(artifact_kind),
        }
        if normalized_visibility == "projection":
            metadata["projection_visible"] = True
        if extra_meta:
            metadata.update(extra_meta)
        return Node(
            id=node_id,
            label=label,
            type="entity",
            summary=summary,
            doc_id=source_document_id,
            mentions=[{"spans": [span.model_dump(field_mode="backend")]}],
            metadata=metadata,
        )

    def _node_exists(self, engine: GraphKnowledgeEngine, *, namespace: str, node_id: str) -> bool:
        with _temporary_namespace(engine, namespace):
            return bool(engine.read.node_exists(ids=[str(node_id)]))

    @staticmethod
    def _document_exists(engine: GraphKnowledgeEngine, document_id: str) -> bool:
        getter = getattr(getattr(engine, "read", None), "get_document", None)
        if not callable(getter):
            return False
        try:
            return getter(str(document_id)) is not None
        except (KeyError, LookupError, ValueError):
            return False

    def _parse_document_id_for_request(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        revision: SourceRevision,
    ) -> str:
        """Use immutable revision documents for every new parse and Span."""
        del request
        # The stable source ID remains a logical locator. Using it as the parse
        # document would make newly emitted spans point at mutable compatibility
        # state instead of the exact bytes being parsed.
        return revision.revision_document_id or source_document_id

    def _job_exists(
        self,
        *,
        namespace: str,
        entity_kind: str,
        entity_id: str,
        job_kind: str,
        payload_matches: Mapping[str, object] | None = None,
    ) -> bool:
        jobs = self.engines.conversation.jobs.list(namespace=namespace, limit=10_000)
        for job in jobs:
            if (
                str(job.entity_kind) == str(entity_kind)
                and str(job.entity_id) == str(entity_id)
                and str(job.job_kind) == str(job_kind)
            ):
                if payload_matches:
                    payload = dict(job.payload)
                    if any(payload.get(key) != expected for key, expected in payload_matches.items()):
                        continue
                return True
        return False

    def _leading_span(self, source_document_id: str, raw_text: str, *, insertion_method: str) -> Span:
        excerpt = (raw_text or " ")[:1]
        return Span.model_validate(
            {
                "collection_page_url": f"document_collection/{source_document_id}",
                "document_page_url": f"document/{source_document_id}",
                "doc_id": source_document_id,
                "insertion_method": insertion_method,
                "page_number": 1,
                "start_char": 0,
                "end_char": 1,
                "excerpt": excerpt,
                "context_before": "",
                "context_after": raw_text[1:81] if len(raw_text) > 1 else "",
                "chunk_id": None,
                "source_cluster_id": None,
            }
        )
