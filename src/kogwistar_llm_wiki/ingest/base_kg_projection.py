"""Project source extraction into the workspace base-knowledge lane."""

from __future__ import annotations

from kogwistar.engine_core.models import GraphExtractionWithIDs, Grounding, Node, Span
from kogwistar.logical_refs import (
    LogicalRef,
    build_reference_edge_payload,
    build_reference_node_payload,
    logical_ref_id,
)

from ..models import IngestPipelineRequest
from ..utils import _temporary_namespace


class BaseKgProjectionMixin:
    """Keep base-KG reference materialization out of ingest orchestration."""

    def _base_kg_reference_span(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        insertion_method: str,
    ) -> Span:
        return Span.model_validate(
            {
                "collection_page_url": f"document_collection/{source_document_id}",
                "document_page_url": f"document/{source_document_id}",
                "doc_id": source_document_id,
                "insertion_method": insertion_method,
                "page_number": 1,
                "start_char": 0,
                "end_char": 1,
                "excerpt": "",
                "context_before": "",
                "context_after": "",
                "chunk_id": None,
                "source_cluster_id": None,
            }
        )

    def _project_base_kg_references(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        source_namespace: str,
        graph_extraction: GraphExtractionWithIDs,
    ) -> None:
        base_namespace = self.namespaces_for(request.workspace_id).base_kg_space
        node_id_map: dict[str, str] = {}

        for node in graph_extraction.nodes or []:
            source_id = str(getattr(node, "id", "") or "")
            if not source_id:
                continue
            ref_id = logical_ref_id(
                scope=request.workspace_id,
                pointer_kind="base_kg_node",
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="node",
                    target_id=source_id,
                ),
            )
            node_id_map[source_id] = ref_id
            payload = build_reference_node_payload(
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="node",
                    target_id=source_id,
                ),
                pointer_kind="base_kg_node",
                pointer_id=ref_id,
                label=str(getattr(node, "label", "") or ""),
                summary=str(getattr(node, "summary", "") or ""),
                graph_space="base_kg",
                extra_properties={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
                extra_metadata={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
            )
            base_node = Node(
                **payload,
                doc_id=source_document_id,
                mentions=[
                    Grounding(
                        spans=[
                            self._base_kg_reference_span(
                                request=request,
                                source_document_id=source_document_id,
                                insertion_method="base_kg_projection",
                            )
                        ]
                    )
                ],
                domain_id=None,
                canonical_entity_id=None,
                embedding=None,
            )
            with _temporary_namespace(self.engines.kg, base_namespace):
                self.engines.kg.write.add_node(base_node)

        for edge in graph_extraction.edges or []:
            source_edge_id = str(getattr(edge, "id", "") or "")
            if not source_edge_id:
                continue
            ref_id = logical_ref_id(
                scope=request.workspace_id,
                pointer_kind="base_kg_edge",
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="edge",
                    target_id=source_edge_id,
                ),
            )
            source_ids = [
                node_id_map.get(str(source_id), str(source_id))
                for source_id in list(getattr(edge, "source_ids", []) or [])
            ]
            target_ids = [
                node_id_map.get(str(target_id), str(target_id))
                for target_id in list(getattr(edge, "target_ids", []) or [])
            ]
            payload = build_reference_edge_payload(
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="edge",
                    target_id=source_edge_id,
                ),
                pointer_kind="base_kg_edge",
                pointer_id=ref_id,
                source_ids=source_ids,
                target_ids=target_ids,
                relation=str(
                    getattr(edge, "relation", "")
                    or getattr(edge, "type", "")
                    or "base_kg_edge"
                ),
                label=str(getattr(edge, "label", "") or "base_kg_edge"),
                summary=str(getattr(edge, "summary", "") or ""),
                graph_space="base_kg",
                extra_properties={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
                extra_metadata={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
            )
            base_edge = type(edge)(
                **payload,
                doc_id=source_document_id,
                mentions=[
                    Grounding(
                        spans=[
                            self._base_kg_reference_span(
                                request=request,
                                source_document_id=source_document_id,
                                insertion_method="base_kg_projection",
                            )
                        ]
                    )
                ],
                domain_id=None,
                canonical_entity_id=None,
                embedding=None,
                source_edge_ids=[],
                target_edge_ids=[],
            )
            with _temporary_namespace(self.engines.kg, base_namespace):
                self.engines.kg.write.add_edge(base_edge)
