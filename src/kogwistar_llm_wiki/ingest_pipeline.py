from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from kogwistar.engine_core.engine import GraphKnowledgeEngine
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar.engine_core.models import Document, Edge, Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar_obsidian_sink.core.models import MentionSpan, ProjectionEntity, SemanticRelationship
from kogwistar_obsidian_sink.core.provider import ProjectionProvider, ProviderSnapshot
from src.workflow_ingest.page_index import PageIndexParseResult, parse_page_index_document

from .models import IngestPipelineArtifacts, IngestPipelineRequest
from .namespaces import WorkspaceNamespaces


class EmbeddingFunctionLike(Protocol):
    def __call__(self, documents_or_texts: Iterable[str]) -> list[list[float]]: ...


class ConstantEmbeddingFunction:
    """Tiny deterministic embedding for tests and local demos.

    This is intentionally app-local and only used when the caller asks this package
    to build in-memory engines. In a real environment the repo install can use the
    upstream embedding/provider configuration instead.
    """

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    @staticmethod
    def name() -> str:
        return "default"

    def __call__(self, documents_or_texts: Iterable[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in documents_or_texts]


@dataclass(frozen=True, slots=True)
class NamespaceEngines:
    conv_fg: GraphKnowledgeEngine
    conv_bg: GraphKnowledgeEngine
    workflow: GraphKnowledgeEngine
    wisdom: GraphKnowledgeEngine
    kg: GraphKnowledgeEngine


@dataclass(slots=True)
class IngestPipeline:
    engines: NamespaceEngines

    def run(self, request: IngestPipelineRequest) -> IngestPipelineArtifacts:
        namespaces = WorkspaceNamespaces(request.workspace_id)
        parse_result = parse_page_index_document(
            document_id=str(stable_id("llm_wiki.source", request.workspace_id, request.source_uri)),
            title=request.title,
            raw_text=request.raw_text,
            source_format=request.source_format,
            mode=request.parser_mode,
        )

        source_document = self._build_document(parse_result=parse_result, request=request)
        self.engines.conv_fg.add_document(source_document)

        source_node = self._build_source_node(
            request=request,
            parse_result=parse_result,
            doc_id=source_document.id,
            namespace=namespaces.conv_fg,
        )
        self.engines.conv_fg.add_node(source_node, doc_id=source_document.id)

        fragment_nodes = tuple(
            self._build_fragment_nodes(
                parse_result=parse_result,
                doc_id=source_document.id,
                namespace=namespaces.conv_fg,
            )
        )
        for node in fragment_nodes:
            self.engines.conv_fg.add_node(node, doc_id=source_document.id)

        maintenance_request = self._build_maintenance_request(
            request=request,
            parse_result=parse_result,
            source_node=source_node,
            namespace=namespaces.workflow_maintenance,
        )
        self.engines.workflow.add_node(maintenance_request, doc_id=source_document.id)

        candidate_links: list[Node] = []
        promotion_candidates: list[Node] = []
        promoted_edges: list[Edge] = []

        existing_kg_nodes = self.engines.kg.get_nodes(node_type=Node, limit=500)
        for fragment in fragment_nodes:
            target = self._pick_crosslink_target(fragment=fragment, existing_nodes=existing_kg_nodes)
            if target is None:
                continue

            candidate = self._build_candidate_link(
                request=request,
                parse_result=parse_result,
                fragment=fragment,
                target=target,
                namespace=namespaces.conv_bg,
            )
            self.engines.conv_bg.add_node(candidate, doc_id=source_document.id)
            candidate_links.append(candidate)

            decision = self._build_promotion_candidate(
                request=request,
                parse_result=parse_result,
                candidate=candidate,
                fragment=fragment,
                target=target,
                namespace=namespaces.review,
                accepted=True,
            )
            self.engines.workflow.add_node(decision, doc_id=source_document.id)
            promotion_candidates.append(decision)

            promoted = self._build_promoted_edge(
                request=request,
                parse_result=parse_result,
                fragment=fragment,
                target=target,
                namespace=namespaces.kg,
            )
            self.engines.kg.add_edge(promoted, doc_id=source_document.id)
            promoted_edges.append(promoted)

        return IngestPipelineArtifacts(
            source_document_id=str(source_document.id),
            source_node_id=str(source_node.id),
            maintenance_request_id=str(maintenance_request.id),
            candidate_link_node_ids=tuple(str(node.id) for node in candidate_links),
            promotion_candidate_node_ids=tuple(str(node.id) for node in promotion_candidates),
            promoted_edge_ids=tuple(str(edge.id) for edge in promoted_edges),
        )

    def build_projection_snapshot(self) -> ProviderSnapshot:
        kg_nodes = self.engines.kg.get_nodes(node_type=Node, limit=500)
        kg_edges = self.engines.kg.get_edges(edge_type=Edge, limit=500)
        edge_by_source: dict[str, list[Edge]] = {}
        for edge in kg_edges:
            for source_id in edge.source_ids:
                edge_by_source.setdefault(source_id, []).append(edge)

        entities: list[ProjectionEntity] = []
        for node in kg_nodes:
            mentions = list(node.iter_span())
            entities.append(
                ProjectionEntity(
                    kg_id=str(node.id),
                    title=node.label,
                    entity_type=node.type,
                    summary=node.summary,
                    metadata=dict(node.metadata or {}),
                    relationships=[
                        SemanticRelationship(
                            source_id=str(node.id),
                            target_id=edge.target_ids[0],
                            relation_type=edge.relation,
                            properties=dict(edge.properties or {}),
                        )
                        for edge in edge_by_source.get(str(node.id), [])
                        if edge.target_ids
                    ],
                    mentions=[
                        MentionSpan(
                            doc_id=span.doc_id,
                            page_number=span.page_number,
                            start_char=span.start_char,
                            end_char=span.end_char,
                            excerpt=span.excerpt,
                            document_page_url=span.document_page_url,
                            context_before=span.context_before,
                            context_after=span.context_after,
                        )
                        for span in mentions
                    ],
                )
            )
        return ProviderSnapshot(entities=entities)

    def _build_document(self, *, parse_result: PageIndexParseResult, request: IngestPipelineRequest) -> Document:
        return Document(
            id=str(stable_id("llm_wiki.document", request.workspace_id, request.source_uri)),
            content=request.raw_text,
            type="text",
            metadata={
                "workspace_id": request.workspace_id,
                "source_uri": request.source_uri,
                "title": request.title,
                "source_format": request.source_format,
                "parser_mode": request.parser_mode,
            },
            processed=True,
            source_map=parse_result.parser_source_map,
        )

    def _build_source_node(
        self,
        *,
        request: IngestPipelineRequest,
        parse_result: PageIndexParseResult,
        doc_id: str,
        namespace: str,
    ) -> Node:
        span = self._root_span(parse_result=parse_result, doc_id=doc_id)
        return Node(
            id=str(stable_id("llm_wiki.source_node", request.workspace_id, request.source_uri)),
            label=request.title,
            type="entity",
            summary=f"Source document registered for ingestion: {request.title}",
            mentions=[Grounding(spans=[span])],
            doc_id=doc_id,
            metadata={
                "namespace": namespace,
                "workspace_id": request.workspace_id,
                "artifact_type": "source_document",
                "conversation_lane": "foreground",
                "origin": "user",
                "visibility": "user",
            },
            properties={"source_uri": request.source_uri, "parser_mode": request.parser_mode},
        )

    def _build_fragment_nodes(
        self,
        *,
        parse_result: PageIndexParseResult,
        doc_id: str,
        namespace: str,
    ) -> Iterable[Node]:
        for page_node in parse_result.semantic_tree.child_nodes:
            for block in page_node.child_nodes:
                excerpt = block.total_content_pointers[0].verbatim_text if block.total_content_pointers else block.title
                span = self._span_from_block(
                    parse_result=parse_result,
                    block=block,
                    doc_id=doc_id,
                    excerpt=excerpt,
                )
                yield Node(
                    id=str(stable_id("llm_wiki.fragment", doc_id, block.title, block.node_type)),
                    label=block.title,
                    type="entity",
                    summary=excerpt[:280],
                    mentions=[Grounding(spans=[span])],
                    doc_id=doc_id,
                    metadata={
                        "namespace": namespace,
                        "artifact_type": "fragment",
                        "conversation_lane": "foreground",
                        "origin": "assistant",
                        "visibility": "user",
                        "fragment_node_type": block.node_type,
                    },
                    properties={
                        "title": block.title,
                        "node_type": block.node_type,
                    },
                )

    def _build_maintenance_request(
        self,
        *,
        request: IngestPipelineRequest,
        parse_result: PageIndexParseResult,
        source_node: Node,
        namespace: str,
    ) -> Node:
        span = self._root_span(parse_result=parse_result, doc_id=str(source_node.doc_id))
        return Node(
            id=str(stable_id("llm_wiki.maintenance_request", request.workspace_id, request.source_uri)),
            label=f"Maintenance request for {request.title}",
            type="entity",
            summary="Schedule ingest_followup and candidate_crosslink for the new source",
            mentions=[Grounding(spans=[span])],
            doc_id=str(source_node.doc_id),
            metadata={
                "namespace": namespace,
                "artifact_type": "maintenance_job_request",
                "job_type": "candidate_crosslink",
                "origin": "maintenance",
                "visibility": "system",
            },
            properties={
                "source_node_id": str(source_node.id),
                "trigger_type": "ingest_completed",
                "policy_version": "ingest-pipeline-v1",
            },
        )

    def _pick_crosslink_target(self, *, fragment: Node, existing_nodes: list[Node]) -> Node | None:
        fragment_key = self._normalize(fragment.label)
        for existing in existing_nodes:
            if self._normalize(existing.label) == fragment_key:
                return existing
        return None

    def _build_candidate_link(
        self,
        *,
        request: IngestPipelineRequest,
        parse_result: PageIndexParseResult,
        fragment: Node,
        target: Node,
        namespace: str,
    ) -> Node:
        span = self._first_span(fragment)
        return Node(
            id=str(stable_id("llm_wiki.candidate_link", request.workspace_id, str(fragment.id), str(target.id))),
            label=f"Candidate link: {fragment.label} -> {target.label}",
            type="entity",
            summary=f"Cross-link candidate proposing {fragment.label} relates to {target.label}",
            mentions=[Grounding(spans=[span])],
            doc_id=str(fragment.doc_id),
            metadata={
                "namespace": namespace,
                "artifact_type": "candidate_link",
                "conversation_lane": "background",
                "origin": "maintenance",
                "visibility": "review",
            },
            properties={
                "source_fragment_id": str(fragment.id),
                "target_node_id": str(target.id),
                "proposed_relation": "related_to",
                "confidence": 1.0,
            },
        )

    def _build_promotion_candidate(
        self,
        *,
        request: IngestPipelineRequest,
        parse_result: PageIndexParseResult,
        candidate: Node,
        fragment: Node,
        target: Node,
        namespace: str,
        accepted: bool,
    ) -> Node:
        span = self._first_span(fragment)
        return Node(
            id=str(stable_id("llm_wiki.promotion_candidate", request.workspace_id, str(candidate.id))),
            label=f"Promotion candidate: {fragment.label} -> {target.label}",
            type="entity",
            summary="Promotion decision artifact for candidate cross-link",
            mentions=[Grounding(spans=[span])],
            doc_id=str(fragment.doc_id),
            metadata={
                "namespace": namespace,
                "artifact_type": "promotion_candidate",
                "origin": "maintenance",
                "visibility": "review",
            },
            properties={
                "candidate_link_id": str(candidate.id),
                "source_fragment_id": str(fragment.id),
                "target_node_id": str(target.id),
                "decision": "accepted" if accepted else "deferred",
                "accepted": accepted,
                "policy_version": "ingest-pipeline-v1",
            },
        )

    def _build_promoted_edge(
        self,
        *,
        request: IngestPipelineRequest,
        parse_result: PageIndexParseResult,
        fragment: Node,
        target: Node,
        namespace: str,
    ) -> Edge:
        span = self._first_span(fragment)
        return Edge(
            id=str(stable_id("llm_wiki.promoted_edge", request.workspace_id, str(fragment.id), str(target.id), "related_to")),
            label=f"{fragment.label} related_to {target.label}",
            type="relationship",
            summary=f"Promoted durable relationship between {fragment.label} and {target.label}",
            relation="related_to",
            source_ids=[str(fragment.id)],
            target_ids=[str(target.id)],
            source_edge_ids=[],
            target_edge_ids=[],
            mentions=[Grounding(spans=[span])],
            doc_id=str(fragment.doc_id),
            metadata={
                "namespace": namespace,
                "artifact_type": "promoted_relation",
                "origin": "maintenance",
                "visibility": "user",
            },
            properties={
                "promoted_from": "candidate_crosslink",
                "workspace_id": request.workspace_id,
            },
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.lower().strip().split())

    @staticmethod
    def _root_span(*, parse_result: PageIndexParseResult, doc_id: str) -> Span:
        first_record = next(iter(parse_result.authoritative_source_map.values()))
        text = getattr(first_record, "text")
        page_number = int(getattr(first_record, "page_number", 1))
        return Span(
            collection_page_url=f"collection/{doc_id}",
            document_page_url=f"document/{doc_id}/page/{page_number}",
            doc_id=doc_id,
            insertion_method="parser",
            page_number=page_number,
            start_char=0,
            end_char=max(1, len(text)),
            excerpt=text,
            context_before="",
            context_after="",
        )

    @staticmethod
    def _first_span(node: Node) -> Span:
        return next(node.iter_span())

    @staticmethod
    def _span_from_block(*, parse_result: PageIndexParseResult, block, doc_id: str, excerpt: str) -> Span:
        pointer = block.total_content_pointers[0]
        source_record = parse_result.authoritative_source_map[pointer.source_cluster_id]
        page_number = int(getattr(source_record, "page_number", 1))
        page_text = getattr(source_record, "text")
        start_char = int(pointer.start_char)
        end_char = int(pointer.end_char) + 1
        return Span(
            collection_page_url=f"collection/{doc_id}",
            document_page_url=f"document/{doc_id}/page/{page_number}",
            doc_id=doc_id,
            insertion_method="parser",
            page_number=page_number,
            start_char=start_char,
            end_char=max(start_char + 1, end_char),
            excerpt=excerpt,
            context_before=page_text[max(0, start_char - 40):start_char],
            context_after=page_text[end_char:end_char + 40],
        )


class SnapshotProjectionProvider(ProjectionProvider):
    def __init__(self, snapshot: ProviderSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> ProviderSnapshot:
        return self._snapshot

    def iter_related_ids(self, entity_id: str) -> Iterable[str]:
        for entity in self._snapshot.entities:
            if entity.kg_id != entity_id:
                continue
            for relation in entity.relationships:
                yield relation.target_id


def build_in_memory_namespace_engines(
    *,
    workspace_id: str,
    persist_root: str,
    embedding_function: EmbeddingFunctionLike | None = None,
) -> NamespaceEngines:
    embedding = embedding_function or ConstantEmbeddingFunction()
    namespaces = WorkspaceNamespaces(workspace_id)
    return NamespaceEngines(
        conv_fg=GraphKnowledgeEngine(
            persist_directory=f"{persist_root}/conv-fg",
            embedding_function=embedding,
            kg_graph_type="conversation",
            namespace=namespaces.conv_fg,
            backend_factory=build_in_memory_backend,
        ),
        conv_bg=GraphKnowledgeEngine(
            persist_directory=f"{persist_root}/conv-bg",
            embedding_function=embedding,
            kg_graph_type="conversation",
            namespace=namespaces.conv_bg,
            backend_factory=build_in_memory_backend,
        ),
        workflow=GraphKnowledgeEngine(
            persist_directory=f"{persist_root}/wf-maintenance",
            embedding_function=embedding,
            kg_graph_type="workflow",
            namespace=namespaces.workflow_maintenance,
            backend_factory=build_in_memory_backend,
        ),
        wisdom=GraphKnowledgeEngine(
            persist_directory=f"{persist_root}/wisdom",
            embedding_function=embedding,
            kg_graph_type="wisdom",
            namespace=namespaces.wisdom,
            backend_factory=build_in_memory_backend,
        ),
        kg=GraphKnowledgeEngine(
            persist_directory=f"{persist_root}/kg",
            embedding_function=embedding,
            kg_graph_type="knowledge",
            namespace=namespaces.kg,
            backend_factory=build_in_memory_backend,
        ),
    )
