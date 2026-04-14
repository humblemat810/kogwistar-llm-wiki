from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import tempfile

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar.engine_core.models import Document, GraphExtractionWithIDs, Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar_obsidian_sink.core.models import ProjectionEntity
from kogwistar_obsidian_sink.integrations.kogwistar_adapter import KogwistarDuckProvider
from kogwistar_obsidian_sink.sinks.obsidian import ObsidianVaultSink
from workflow_ingest.page_index import PageIndexParseResult, parse_page_index_document
from workflow_ingest.semantics import semantic_tree_to_kge_payload

from .models import IngestPipelineArtifacts, IngestPipelineRequest, ObsidianBuildResult
from .namespaces import WorkspaceNamespaces


class _TinyEmbeddingFunction:
    _name = "kogwistar-llm-wiki-embedding-v1"

    def name(self) -> str:
        return self._name

    def __call__(self, values: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for value in values:
            text = str(value or "")
            checksum = float((sum(ord(ch) for ch in text) % 97) + 1)
            vectors.append([float(len(text) + 1), checksum])
        return vectors


@dataclass(slots=True)
class NamespaceEngines:
    conversation: Any
    workflow: Any
    kg: Any
    wisdom: Any


@dataclass(slots=True)
class ProjectionSnapshot:
    entities: list[ProjectionEntity]


ParserFn = Callable[..., PageIndexParseResult]


def build_in_memory_namespace_engines(base_dir: str | Path | None = None) -> NamespaceEngines:
    root = Path(base_dir) if base_dir is not None else Path(tempfile.mkdtemp(prefix="kogwistar-llm-wiki-"))
    embedding = _TinyEmbeddingFunction()
    return NamespaceEngines(
        conversation=_build_engine(root / "conversation", kg_graph_type="conversation", embedding_function=embedding),
        workflow=_build_engine(root / "workflow", kg_graph_type="workflow", embedding_function=embedding),
        kg=_build_engine(root / "kg", kg_graph_type="knowledge", embedding_function=embedding),
        wisdom=_build_engine(root / "wisdom", kg_graph_type="wisdom", embedding_function=embedding),
    )


def _build_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: Any,
) -> GraphKnowledgeEngine:
    persist_directory.mkdir(parents=True, exist_ok=True)
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        backend_factory=build_in_memory_backend,
        namespace=kg_graph_type,
    )


@contextmanager
def _temporary_namespace(engine: Any, namespace: str):
    previous = getattr(engine, "namespace", None)
    engine.namespace = namespace
    try:
        yield engine
    finally:
        engine.namespace = previous


class IngestPipeline:
    """Thin app-level orchestration over parser -> Kogwistar ingest -> maintenance -> projection.

    This class deliberately avoids owning parser semantics. It coordinates:
    - source registration in conversation foreground
    - parser invocation via kg-doc-parser
    - conversion of parser result into Kogwistar graph extraction payload
    - persistence via Kogwistar document + graph extraction APIs
    - workflow request creation
    - maintenance candidates in conversation lanes
    - optional KG promotion
    """

    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        parser: ParserFn = parse_page_index_document,
    ) -> None:
        self.engines = engines
        self.parser = parser

    def namespaces_for(self, workspace_id: str) -> WorkspaceNamespaces:
        return WorkspaceNamespaces(workspace_id)

    def run(self, request: IngestPipelineRequest) -> IngestPipelineArtifacts:
        ns = self.namespaces_for(request.workspace_id)
        source_document_id = self._source_document_id(request)
        self.register_source(request=request, source_document_id=source_document_id, namespace=ns.conv_fg)
        parse_result = self.parse_source(request=request, source_document_id=source_document_id)
        graph_extraction = self.translate_parse_result(parse_result=parse_result, source_document_id=source_document_id)
        self.ingest_parse_result(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=graph_extraction,
            namespace=ns.conv_fg,
        )
        maintenance_job_id = self.create_maintenance_request(
            request=request,
            source_document_id=source_document_id,
            namespace=ns.workflow_maintenance,
        )
        candidate_link_id = self.create_candidate_link(
            request=request,
            source_document_id=source_document_id,
            parse_result=parse_result,
            namespace=ns.conv_bg,
        )
        promotion_candidate_id = self.create_promotion_candidate(
            request=request,
            source_document_id=source_document_id,
            candidate_link_id=candidate_link_id,
            namespace=ns.review,
        )

        promoted_entity_id: str | None = None
        promotion_confidence = 0.95
        if promotion_confidence >= request.auto_accept_threshold:
            promoted_entity_id = self.promote_to_knowledge(
                request=request,
                source_document_id=source_document_id,
                promotion_candidate_id=promotion_candidate_id,
                namespace=ns.kg,
            )

        return IngestPipelineArtifacts(
            source_document_id=source_document_id,
            maintenance_job_id=maintenance_job_id,
            candidate_link_id=candidate_link_id,
            promotion_candidate_id=promotion_candidate_id,
            promoted_entity_id=promoted_entity_id,
        )

    def build_obsidian_vault(
        self,
        vault_root: str | Path,
        *,
        workspace_id: str | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        entities = self._projection_visible_nodes(workspace_id=workspace_id)
        provider = KogwistarDuckProvider(
            entities=entities,
            version=version,
            event_seq=event_seq,
        )
        sink = ObsidianVaultSink(vault_root=vault_root)
        result = sink.build(provider)
        return ObsidianBuildResult(
            vault_root=Path(vault_root),
            notes=int(result.get("notes", 0)),
            canvases=int(result.get("canvases", 0)),
            dangling_links=int(result.get("dangling_links", 0)),
        )

    def sync_obsidian_vault(
        self,
        vault_root: str | Path,
        *,
        workspace_id: str | None = None,
        changed_ids: set[str] | None = None,
        deleted_ids: set[str] | None = None,
        affected_titles: set[str] | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        entities = self._projection_visible_nodes(workspace_id=workspace_id)
        provider = KogwistarDuckProvider(
            entities=entities,
            version=version,
            event_seq=event_seq,
        )
        sink = ObsidianVaultSink(vault_root=vault_root)
        result = sink.sync(
            provider,
            changed_ids=changed_ids,
            deleted_ids=deleted_ids,
            affected_titles=affected_titles,
        )
        return ObsidianBuildResult(
            vault_root=Path(vault_root),
            notes=int(result.get("updated_notes", result.get("notes", 0))),
            canvases=int(result.get("updated_canvases", result.get("canvases", 0))),
            dangling_links=int(result.get("dangling_links", 0)),
        )

    def _projection_visible_nodes(self, *, workspace_id: str | None = None) -> list[Node]:
        where: dict[str, Any] = {"projection_visible": True}
        if workspace_id is not None:
            where["workspace_id"] = workspace_id
        return list(self.engines.kg.get_nodes(where=where))

    @staticmethod
    def _node_to_projection_entity(node: Node) -> ProjectionEntity:
        return ProjectionEntity(
            kg_id=str(node.id),
            title=str(node.label),
            entity_type=str(node.type),
            summary=str(node.summary),
            metadata=dict(node.metadata or {}),
            source_ids=list(getattr(node, "source_ids", []) or []),
            target_ids=list(getattr(node, "target_ids", []) or []),
            relation=getattr(node, "relation", None),
            body=str(node.summary),
        )

    def _source_document_id(self, request: IngestPipelineRequest) -> str:
        return str(
            stable_id(
                "kogwistar_llm_wiki.source_document",
                request.workspace_id,
                request.source_uri,
                request.title,
            )
        )

    def register_source(self, *, request: IngestPipelineRequest, source_document_id: str, namespace: str) -> None:
        document = Document(
            id=source_document_id,
            content=request.raw_text,
            type="text",
            metadata={
                "workspace_id": request.workspace_id,
                "source_uri": request.source_uri,
                "title": request.title,
                "source_format": request.source_format,
                "parser_mode": request.parser_mode,
            },
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_document(document)

    def parse_source(self, *, request: IngestPipelineRequest, source_document_id: str) -> PageIndexParseResult:
        return self.parser(
            document_id=source_document_id,
            title=request.title,
            raw_text=request.raw_text,
            source_format=request.source_format,
            mode=request.parser_mode,
        )

    def translate_parse_result(
        self,
        *,
        parse_result: PageIndexParseResult,
        source_document_id: str,
    ) -> GraphExtractionWithIDs:
        payload = semantic_tree_to_kge_payload(parse_result.semantic_tree, doc_id=source_document_id)
        return GraphExtractionWithIDs.model_validate(payload)

    def ingest_parse_result(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        namespace: str,
    ) -> None:
        del request
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.persist_document_graph_extraction(
                doc_id=source_document_id,
                parsed=graph_extraction,
                mode="append",
            )

    def create_maintenance_request(self, *, request: IngestPipelineRequest, source_document_id: str, namespace: str) -> str:
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            artifact_kind="maintenance_job_request",
            lane="workflow",
            visibility="system",
            label=f"Maintenance request: {request.title}",
            summary=f"Queue follow-up maintenance for {request.title}",
        )
        with _temporary_namespace(self.engines.workflow, namespace):
            self.engines.workflow.write.add_node(node)
        return str(node.id)

    def create_candidate_link(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        parse_result: PageIndexParseResult,
        namespace: str,
    ) -> str:
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            artifact_kind="candidate_link",
            lane="background",
            visibility="review",
            label=f"Candidate link: {request.title}",
            summary=f"Candidate link derived from {parse_result.semantic_tree.title}",
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_node(node)
        return str(node.id)

    def create_promotion_candidate(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        candidate_link_id: str,
        namespace: str,
    ) -> str:
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            artifact_kind="promotion_candidate",
            lane="background",
            visibility="review",
            label=f"Promotion candidate: {request.title}",
            summary=f"Promotion candidate linked from {candidate_link_id}",
            extra_metadata={"candidate_link_id": candidate_link_id},
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_node(node)
        return str(node.id)

    def promote_to_knowledge(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        promotion_candidate_id: str,
        namespace: str,
    ) -> str:
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            artifact_kind="promoted_knowledge",
            lane="knowledge",
            visibility="projection",
            label=request.title,
            summary=f"Promoted knowledge derived from {request.title}",
            extra_metadata={
                "projection_visible": True,
                "promotion_candidate_id": promotion_candidate_id,
            },
        )
        with _temporary_namespace(self.engines.kg, namespace):
            self.engines.kg.write.add_node(node)
        return str(node.id)

    def build_projection_snapshot(self, workspace_id: str | None = None) -> ProjectionSnapshot:
        where: dict[str, Any] = {"projection_visible": True}
        if workspace_id is not None:
            where["workspace_id"] = workspace_id
        visible_nodes = list(self.engines.kg.get_nodes(where=where))
        visible_nodes.sort(key=lambda node: (str(node.label), str(node.id)))
        return ProjectionSnapshot(
            entities=[
                ProjectionEntity(
                    kg_id=str(node.id),
                    title=str(node.label),
                    entity_type=str(node.type),
                    summary=str(node.summary),
                    metadata=dict(node.metadata or {}),
                    source_ids=list(getattr(node, "source_ids", []) or []),
                    target_ids=list(getattr(node, "target_ids", []) or []),
                    relation=getattr(node, "relation", None),
                    body=str(node.summary),
                )
                for node in visible_nodes
            ]
        )

    def _artifact_node(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
        artifact_kind: str,
        lane: str,
        visibility: str,
        label: str,
        summary: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> Node:
        span = self._leading_span(source_document_id, request.raw_text, insertion_method=artifact_kind)
        metadata = {
            "workspace_id": request.workspace_id,
            "source_document_id": source_document_id,
            "source_uri": request.source_uri,
            "artifact_kind": artifact_kind,
            "namespace": namespace,
            "conversation_lane": lane,
            "visibility": visibility,
            "title": request.title,
            "parser_mode": request.parser_mode,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        return Node(
            label=label,
            type="entity",
            summary=summary,
            doc_id=source_document_id,
            mentions=[Grounding(spans=[span])],
            metadata=metadata,
        )

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
