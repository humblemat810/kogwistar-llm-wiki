"""Translate and persist parser graph results with source grounding guards."""

from __future__ import annotations

import json

from kg_doc_parser.workflow_ingest.semantics import semantic_tree_to_kge_payload
from kogwistar.engine_core.models import GraphExtractionWithIDs
from kogwistar.id_provider import stable_id

from ..models import IngestPipelineRequest
from ..parsing.parse_views import SourceRegion
from ..utils import _temporary_namespace


class GraphPersistenceMixin:
    """Keep parser output validation and graph writes out of orchestration."""

    def translate_parse_result(
        self,
        *,
        parse_result: object,
        source_document_id: str,
    ) -> GraphExtractionWithIDs:
        self._trace_event(
            "translate_parse_result_start",
            source_document_id=source_document_id,
            semantic_title=getattr(parse_result.semantic_tree, "title", None),
        )
        graph_payload = getattr(parse_result, "graph_payload", None)
        if graph_payload is not None:
            payload = dict(graph_payload)
            payload["doc_id"] = source_document_id
            result = GraphExtractionWithIDs.model_validate(payload)
        else:
            payload = semantic_tree_to_kge_payload(
                parse_result.semantic_tree,
                doc_id=source_document_id,
            )
            result = GraphExtractionWithIDs.model_validate(payload)
        self._trace_event(
            "translate_parse_result_complete",
            source_document_id=source_document_id,
            node_count=len(getattr(result, "nodes", []) or []),
            edge_count=len(getattr(result, "edges", []) or []),
        )
        return result

    def _repair_graph_extraction_spans(
        self,
        *,
        graph_extraction: GraphExtractionWithIDs,
        source_document_id: str,
        request: IngestPipelineRequest,
    ) -> dict[str, int]:
        """Repair uniquely recoverable offsets before graph persistence."""

        source_namespace = self.namespaces_for(request.workspace_id).source_space
        repaired_count = 0
        rejected_count = 0
        with _temporary_namespace(self.engines.kg, source_namespace):
            document = self.engines.kg.read.get_document(source_document_id)
            validator = self.engines.kg.get_span_validator_of_doc_type(document=document)
            for entity in [*(graph_extraction.nodes or []), *(graph_extraction.edges or [])]:
                for grounding in entity.mentions or []:
                    for index, span in enumerate(list(grounding.spans or [])):
                        repaired, diagnostics = validator.repair_span(span, doc=document)
                        final_validation = validator.validate_span(repaired, doc=document)
                        if final_validation.get("correctness") is not True:
                            rejected_count += 1
                            raise ValueError(
                                "source span could not be safely repaired before persistence: "
                                f"doc_id={source_document_id!r} "
                                f"start={span.start_char} end={span.end_char} "
                                f"excerpt={span.excerpt!r} "
                                f"extracted={final_validation.get('excerpt_from_start_end_index')!r} "
                                f"diagnostics={diagnostics}"
                            )
                        if diagnostics.get("repaired") is True:
                            repaired_count += 1
                            grounding.spans[index] = repaired
        if repaired_count or rejected_count:
            self._trace_event(
                "source_span_repair_complete",
                source_document_id=source_document_id,
                repaired_count=repaired_count,
                rejected_count=rejected_count,
            )
        return {"repaired_count": repaired_count, "rejected_count": rejected_count}

    @staticmethod
    def _validate_parse_region_spans(
        *,
        graph_extraction: GraphExtractionWithIDs,
        source_document_id: str,
        parse_region: SourceRegion,
    ) -> None:
        """Reject grounded output that escapes a targeted reparse region."""

        for entity in [*(graph_extraction.nodes or []), *(graph_extraction.edges or [])]:
            for mention in entity.mentions or []:
                for span in mention.spans or []:
                    if span.doc_id != source_document_id:
                        raise ValueError(
                            "targeted parse span points to a different revision document: "
                            f"expected={source_document_id!r} got={span.doc_id!r}"
                        )
                    if (
                        span.start_char < parse_region.start_char
                        or span.end_char > parse_region.end_char
                    ):
                        raise ValueError(
                            "targeted parse span escapes its requested source region: "
                            f"span=({span.start_char},{span.end_char}) "
                            f"region=({parse_region.start_char},{parse_region.end_char})"
                        )

    def ingest_parse_result(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        namespace: str,
        parse_generation_id: str | None = None,
        parse_generation_member_id: str | None = None,
        parse_region: SourceRegion | None = None,
    ) -> None:
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        if parse_generation_member_id:
            graph_extraction = self._scope_generation_extraction_ids(
                graph_extraction,
                parse_generation_member_id=parse_generation_member_id,
            )
        if parse_region is not None:
            self._validate_parse_region_spans(
                graph_extraction=graph_extraction,
                source_document_id=source_document_id,
                parse_region=parse_region,
            )
        self._trace_step(
            "ingest_parse_result_start",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_count=len(getattr(graph_extraction, "nodes", []) or []),
            edge_count=len(getattr(graph_extraction, "edges", []) or []),
        )
        source_parsed = self._source_graph_extraction(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=graph_extraction,
            parse_generation_id=parse_generation_id,
            parse_generation_member_id=parse_generation_member_id,
            parse_region=parse_region,
        )
        self._repair_graph_extraction_spans(
            graph_extraction=source_parsed,
            source_document_id=source_document_id,
            request=request,
        )
        compatibility_parsed = self._source_graph_extraction(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=source_parsed,
            legacy_namespace=namespace,
            parse_generation_id=parse_generation_id,
            parse_generation_member_id=parse_generation_member_id,
            parse_region=parse_region,
        )

        with _temporary_namespace(self.engines.kg, source_namespace):
            self.engines.kg.persist_document_graph_extraction(
                doc_id=source_document_id,
                parsed=source_parsed,
                mode="append",
            )
        self._trace_step(
            "ingest_parse_result_persisted_source",
            request=request,
            source_document_id=source_document_id,
            namespace=str(source_namespace),
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.persist_document_graph_extraction(
                doc_id=source_document_id,
                parsed=compatibility_parsed,
                mode="append",
            )
        self._trace_step(
            "ingest_parse_result_persisted_compatibility",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
        )
        self._project_base_kg_references(
            request=request,
            source_document_id=source_document_id,
            source_namespace=source_namespace,
            graph_extraction=source_parsed,
        )
        self._trace_step(
            "ingest_parse_result_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
        )

    @staticmethod
    def _scope_generation_extraction_ids(
        graph_extraction: GraphExtractionWithIDs,
        *,
        parse_generation_member_id: str,
    ) -> GraphExtractionWithIDs:
        """Give derived evidence event IDs scoped to one generation member."""

        enriched = graph_extraction.model_copy(deep=True)
        node_ids: dict[str, str] = {}
        for ordinal, node in enumerate(enriched.nodes or []):
            semantic_id = str(getattr(node, "id", "") or "")
            shape = node.model_dump(dump_format="json", exclude={"id"})
            event_id = str(
                stable_id(
                    "kogwistar_llm_wiki.parse_generation_node",
                    parse_generation_member_id,
                    str(ordinal),
                    json.dumps(shape, sort_keys=True, separators=(",", ":")),
                )
            )
            if semantic_id:
                node_ids[semantic_id] = event_id
            node.metadata = {
                **dict(getattr(node, "metadata", {}) or {}),
                "semantic_id": semantic_id or None,
                "parse_generation_event_id": event_id,
            }
            node.id = event_id

        edge_ids: dict[str, str] = {}
        for ordinal, edge in enumerate(enriched.edges or []):
            semantic_id = str(getattr(edge, "id", "") or "")
            shape = edge.model_dump(dump_format="json", exclude={"id"})
            event_id = str(
                stable_id(
                    "kogwistar_llm_wiki.parse_generation_edge",
                    parse_generation_member_id,
                    str(ordinal),
                    json.dumps(shape, sort_keys=True, separators=(",", ":")),
                )
            )
            if semantic_id:
                edge_ids[semantic_id] = event_id
            edge.metadata = {
                **dict(getattr(edge, "metadata", {}) or {}),
                "semantic_id": semantic_id or None,
                "parse_generation_event_id": event_id,
            }
            edge.id = event_id

        for edge in enriched.edges or []:
            edge.source_ids = [node_ids.get(value, value) for value in edge.source_ids]
            edge.target_ids = [node_ids.get(value, value) for value in edge.target_ids]
            edge.source_edge_ids = [
                edge_ids.get(value, value) for value in (edge.source_edge_ids or [])
            ]
            edge.target_edge_ids = [
                edge_ids.get(value, value) for value in (edge.target_edge_ids or [])
            ]
        return enriched

    def _source_graph_extraction(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        legacy_namespace: str | None = None,
        parse_generation_id: str | None = None,
        parse_generation_member_id: str | None = None,
        parse_region: SourceRegion | None = None,
    ) -> GraphExtractionWithIDs:
        enriched = graph_extraction.model_copy(deep=True)
        logical_source_document_id = self._source_document_id(request)
        metadata = {
            "workspace_id": request.workspace_id,
            "graph_space": "source",
            "source_document_id": logical_source_document_id,
            "source_revision_document_id": source_document_id,
            "source_uri": request.source_uri,
        }
        if parse_generation_id:
            metadata["parse_generation_id"] = parse_generation_id
        if parse_generation_member_id:
            metadata["parse_generation_member_id"] = parse_generation_member_id
        if parse_region is not None:
            metadata["parse_region"] = parse_region.model_dump(mode="json")
        for node in enriched.nodes:
            node_metadata = dict(getattr(node, "metadata", {}) or {})
            node_metadata.update(metadata)
            if legacy_namespace is not None:
                node_metadata["legacy_namespace"] = legacy_namespace
            node.metadata = node_metadata
        for edge in enriched.edges:
            edge_metadata = dict(getattr(edge, "metadata", {}) or {})
            edge_metadata.update(metadata)
            if legacy_namespace is not None:
                edge_metadata["legacy_namespace"] = legacy_namespace
            edge.metadata = edge_metadata
        return enriched
