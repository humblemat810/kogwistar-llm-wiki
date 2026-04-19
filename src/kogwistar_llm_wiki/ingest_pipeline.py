from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path
import tempfile
from typing import Any, Callable

from .utils import _temporary_namespace
from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar.engine_core.models import Document, GraphExtractionWithIDs, Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kg_doc_parser.workflow_ingest.page_index import PageIndexParseResult, parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import ProviderEndpointConfig, WorkflowProviderSettings
from kg_doc_parser.workflow_ingest.semantics import semantic_tree_to_kge_payload
from .models import (
    IngestPipelineArtifacts,
    IngestPipelineRequest,
    ObsidianBuildResult,
    NamespaceEngines,
    ProjectionSnapshot,
)
from .namespaces import WorkspaceNamespaces
from .projection import ProjectionManager


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





ParserFn = Callable[..., PageIndexParseResult]


def build_in_memory_namespace_engines(
    base_dir: str | Path | None = None,
    *,
    split_derived_knowledge: bool = False,
) -> NamespaceEngines:
    root = Path(base_dir) if base_dir is not None else Path(tempfile.mkdtemp(prefix="kogwistar-llm-wiki-"))
    embedding = _TinyEmbeddingFunction()
    
    # Shared conversation engine (fg/bg lanes)
    conversation = _build_engine(root / "conversation", kg_graph_type="conversation", embedding_function=embedding)
    
    derived_engine = _build_engine(root / "derived_knowledge", kg_graph_type="derived_knowledge", embedding_function=embedding) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=conversation,
        workflow=_build_engine(root / "workflow", kg_graph_type="workflow", embedding_function=embedding),
        kg=_build_engine(root / "kg", kg_graph_type="knowledge", embedding_function=embedding),
        wisdom=_build_engine(root / "wisdom", kg_graph_type="wisdom", embedding_function=embedding),
        derived_knowledge=derived_engine,
    )


def build_persistent_namespace_engines(
    base_dir: str | Path,
    *,
    split_derived_knowledge: bool = False,
) -> NamespaceEngines:
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    embedding = _TinyEmbeddingFunction()
    derived_engine = _build_persistent_engine(root / "derived_knowledge", kg_graph_type="derived_knowledge", embedding_function=embedding) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=_build_persistent_engine(root / "conversation", kg_graph_type="conversation", embedding_function=embedding),
        workflow=_build_persistent_engine(root / "workflow", kg_graph_type="workflow", embedding_function=embedding),
        kg=_build_persistent_engine(root / "kg", kg_graph_type="knowledge", embedding_function=embedding),
        wisdom=_build_persistent_engine(root / "wisdom", kg_graph_type="wisdom", embedding_function=embedding),
        derived_knowledge=derived_engine,
    )


def build_postgres_namespace_engines(
    *,
    base_dir: str | Path,
    dsn: str,
    embedding_dim: int = 2,
    schema: str = "public",
    split_derived_knowledge: bool = False,
) -> NamespaceEngines:
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    embedding = _TinyEmbeddingFunction()
    derived_engine = _build_postgres_engine(
        root / "derived_knowledge",
        kg_graph_type="derived_knowledge",
        embedding_function=embedding,
        dsn=dsn,
        embedding_dim=embedding_dim,
        schema=schema,
    ) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=_build_postgres_engine(
            root / "conversation",
            kg_graph_type="conversation",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        workflow=_build_postgres_engine(
            root / "workflow",
            kg_graph_type="workflow",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        kg=_build_postgres_engine(
            root / "kg",
            kg_graph_type="knowledge",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        wisdom=_build_postgres_engine(
            root / "wisdom",
            kg_graph_type="wisdom",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        derived_knowledge=derived_engine,
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


def _build_persistent_engine(
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
        namespace=kg_graph_type,
    )


def _build_postgres_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: Any,
    dsn: str,
    embedding_dim: int,
    schema: str,
) -> GraphKnowledgeEngine:
    from kogwistar.engine_core.engine_postgres import EnginePostgresConfig, build_postgres_backend

    persist_directory.mkdir(parents=True, exist_ok=True)
    backend, _ = build_postgres_backend(
        EnginePostgresConfig(
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        )
    )
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        backend=backend,
        namespace=kg_graph_type,
    )


class IngestPipeline:
    # Threshold above which a promotion_candidate is auto-accepted in 'sync' mode.
    # TODO: make this configurable per-workspace or per-document-type.
    _AUTO_ACCEPT_THRESHOLD = 0.95

    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        parser: ParserFn = parse_page_index_document,
    ) -> None:
        self.engines = engines
        self.parser = parser
        self.projection = ProjectionManager(engines)

    def namespaces_for(self, workspace_id: str) -> WorkspaceNamespaces:
        return WorkspaceNamespaces(workspace_id)

    def run(self, request: IngestPipelineRequest) -> IngestPipelineArtifacts:
        ns = self.namespaces_for(request.workspace_id)
        source_document_id = self._source_document_id(request)

        self.register_source(
            request=request,
            source_document_id=source_document_id,
            namespace=ns.conv_fg,
        )
        parse_result = self.parse_source(
            request=request,
            source_document_id=source_document_id,
        )
        graph_extraction = self.translate_parse_result(
            parse_result=parse_result,
            source_document_id=source_document_id,
        )
        self.ingest_parse_result(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=graph_extraction,
            namespace=ns.conv_fg,
        )
        maintenance_job_id = self.create_maintenance_request(
            request=request,
            source_document_id=source_document_id,
            namespace=ns.conv_bg,
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
            namespace=ns.conv_bg,
        )


        promoted_entity_id: str | None = None
        if request.promotion_mode == "sync" and self._AUTO_ACCEPT_THRESHOLD >= request.auto_accept_threshold:
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
        workspace_id: str,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        return self.projection.build_obsidian_vault(
            vault_root,
            workspace_id=workspace_id,
            version=version,
            event_seq=event_seq,
        )

    def sync_obsidian_vault(
        self,
        vault_root: str | Path,
        *,
        workspace_id: str,
        changed_ids: set[str] | None = None,
        deleted_ids: set[str] | None = None,
        affected_titles: set[str] | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        return self.projection.sync_obsidian_vault(
            vault_root,
            workspace_id=workspace_id,
            changed_ids=changed_ids,
            deleted_ids=deleted_ids,
            affected_titles=affected_titles,
            version=version,
            event_seq=event_seq,
        )

    def _source_document_id(self, request: IngestPipelineRequest) -> str:
        return str(
            stable_id(
                "kogwistar_llm_wiki.source_document",
                request.workspace_id,
                request.source_uri,
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
        parser_kwargs = self._build_parser_kwargs(request=request, source_document_id=source_document_id)
        return self.parser(**parser_kwargs)

    def _build_parser_kwargs(self, *, request: IngestPipelineRequest, source_document_id: str) -> dict[str, Any]:
        parser_kwargs: dict[str, Any] = {
            "document_id": source_document_id,
            "title": request.title,
            "raw_text": request.raw_text,
            "source_format": request.source_format,
            "mode": request.parser_mode,
        }

        if request.parser_mode == "heuristic":
            return parser_kwargs

        provider = request.llm_provider or self._provider_from_mode(request.parser_mode)
        model = request.llm_model or self._model_from_env(provider)
        if provider is None:
            raise ValueError(f"missing llm provider for parser_mode={request.parser_mode!r}")

        sig = inspect.signature(self.parser)
        params = sig.parameters
        supports_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())

        if "llm_provider" in params or supports_kwargs:
            parser_kwargs["llm_provider"] = provider
        if "model" in params or supports_kwargs:
            parser_kwargs["model"] = model
        if "provider_settings" in params or supports_kwargs:
            parser_kwargs["provider_settings"] = WorkflowProviderSettings(
                parser=ProviderEndpointConfig(
                    provider=provider,
                    model=model,
                )
            )

        if not supports_kwargs and not any(
            key in parser_kwargs for key in ("llm_provider", "model", "provider_settings")
        ):
            raise ValueError(
                "Configured parser does not expose llm_provider/model or provider_settings; "
                "upgrade kg-doc-parser or provide a compatible parser callable."
            )

        return parser_kwargs

    @staticmethod
    def _provider_from_mode(mode: str) -> str | None:
        if mode in {"ollama", "gemini"}:
            return mode
        return None

    @staticmethod
    def _model_from_env(provider: str | None) -> str | None:
        if provider == "ollama":
            return os.getenv("OLLAMA_MODEL") or os.getenv("KG_DOC_PARSER_MODEL")
        if provider == "gemini":
            return os.getenv("GEMINI_MODEL") or os.getenv("KG_DOC_PARSER_MODEL")
        return os.getenv("KG_DOC_PARSER_MODEL")

    def translate_parse_result(
        self,
        *,
        parse_result: PageIndexParseResult,
        source_document_id: str,
    ) -> GraphExtractionWithIDs:
        payload = semantic_tree_to_kge_payload(parse_result.semantic_tree, doc_id=source_document_id)
        return GraphExtractionWithIDs.model_validate(payload)

    def persist_demo_graph_extraction(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        namespace: str,
    ) -> None:
        """Persist a KG-visible copy of the semantic tree for the one-process demo.

        The regular ingestion path keeps its existing promotion behavior. The demo path
        intentionally mirrors the full semantic tree into KG so the graph view has a
        richer node/edge structure while remaining single-process and ephemeral.
        """
        enriched = self._filter_demo_graph_extraction(graph_extraction)
        kg_document = Document(
            id=source_document_id,
            content=request.raw_text,
            type="text",
            metadata={
                "workspace_id": request.workspace_id,
                "source_uri": request.source_uri,
                "title": request.title,
                "source_format": request.source_format,
                "parser_mode": request.parser_mode,
                "visibility": "projection",
                "projection_visible": True,
                "demo_graph_extraction": True,
            },
        )
        for node in enriched.nodes:
            metadata = dict(getattr(node, "metadata", {}) or {})
            metadata.update(
                {
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_uri": request.source_uri,
                    "visibility": "projection",
                    "projection_visible": True,
                    "demo_graph_extraction": True,
                }
            )
            node.metadata = metadata
        for edge in enriched.edges:
            metadata = dict(getattr(edge, "metadata", {}) or {})
            metadata.update(
                {
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_uri": request.source_uri,
                    "visibility": "projection",
                    "projection_visible": True,
                    "demo_graph_extraction": True,
                }
            )
            edge.metadata = metadata
        with _temporary_namespace(self.engines.kg, namespace):
            self.engines.kg.write.add_document(kg_document)
            self.engines.kg.persist_document_graph_extraction(
                doc_id=source_document_id,
                parsed=enriched,
                mode="append",
            )

    @staticmethod
    def _is_sentence_like_title(title: str) -> bool:
        text = str(title or "").strip()
        if text.startswith("This is a starter document for the LLM-Wiki quickstart"):
            return True
        if len(text) < 32:
            return False
        if text.endswith((".", "!", "?")):
            return True
        return bool(re.search(r"\s{3,}", text))

    def _filter_demo_graph_extraction(self, graph_extraction: GraphExtractionWithIDs) -> GraphExtractionWithIDs:
        """Drop sentence-like leaf nodes from the demo graph while keeping hyperedge structure."""
        enriched = graph_extraction.model_copy(deep=True)
        parent_by_child: dict[str, str] = {}
        for edge in enriched.edges:
            sources = [str(item) for item in (getattr(edge, "source_ids", None) or []) if str(item)]
            targets = [str(item) for item in (getattr(edge, "target_ids", None) or []) if str(item)]
            if not sources or not targets:
                continue
            for source_id in sources:
                for target_id in targets:
                    parent_by_child[target_id] = source_id

        retained_nodes = []
        removed_ids: set[str] = set()
        for node in enriched.nodes:
            node_id = str(getattr(node, "id", "") or "")
            if not node_id:
                continue
            title = str(getattr(node, "label", "") or getattr(node, "summary", "") or "")
            semantic_type = str((getattr(node, "metadata", {}) or {}).get("semantic_node_type") or "")
            is_leaf = node_id not in parent_by_child
            if title.startswith("This is a starter document for the LLM-Wiki quickstart"):
                removed_ids.add(node_id)
                continue
            if (
                semantic_type not in {"DOCUMENT_ROOT"}
                and is_leaf
                and self._is_sentence_like_title(title)
            ):
                removed_ids.add(node_id)
                continue
            retained_nodes.append(node)

        if not removed_ids:
            return enriched

        retained_edges = []
        for edge in enriched.edges:
            sources = [str(item) for item in (getattr(edge, "source_ids", None) or []) if str(item)]
            targets = [str(item) for item in (getattr(edge, "target_ids", None) or []) if str(item)]
            if any(source in removed_ids for source in sources) or any(target in removed_ids for target in targets):
                continue
            retained_edges.append(edge)

        enriched.nodes = retained_nodes
        enriched.edges = retained_edges
        return enriched

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
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance_request",
                request.workspace_id,
                source_document_id,
            )
        )
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_id=node_id,
            artifact_kind="maintenance_job_request",
            lane="background",
            visibility="internal",
            label="Maintenance Job Request",
            summary=f"Maintenance requested for {request.title}",
            extra_metadata={
                "job_type": "distillation",
                "trigger_type": "ingest",
                "status": "pending",
            },
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_node(node)
            lane_message = self.engines.conversation.send_lane_message(
                conversation_id=f"maintenance:{source_document_id}",
                inbox_id="inbox:worker:maintenance",
                sender_id="lane:foreground",
                recipient_id="lane:worker:maintenance",
                msg_type="request.maintenance",
                payload={
                    "workspace_id": request.workspace_id,
                    "request_node_id": str(node.id),
                    "source_document_id": source_document_id,
                    "maintenance_kind": "distill",
                },
            )
        self._enqueue_maintenance_job(
            request=request,
            request_node_id=str(node.id),
            source_document_id=source_document_id,
            namespace=self.namespaces_for(request.workspace_id).maintenance_jobs,
            lane_message_id=lane_message.message_id,
        )
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
            extra_metadata={
                "candidate_link_id": candidate_link_id,
                "promotion_mode": request.promotion_mode,
                "queue_state": "pending",
                "queue_previous_id": None,
                "queue_next_id": None,
                "lineage_source_ids": [source_document_id, candidate_link_id],
                "review_namespace": self.namespaces_for(request.workspace_id).review,
            },
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
        self._enqueue_projection_job(
            request=request,
            promoted_id=str(node.id),
            namespace=self.namespaces_for(request.workspace_id).projection_jobs,
        )
        return str(node.id)

    def _enqueue_maintenance_job(
        self,
        *,
        request: IngestPipelineRequest,
        request_node_id: str,
        source_document_id: str,
        namespace: str,
        lane_message_id: str | None = None,
    ) -> str:
        payload = {
            "workspace_id": request.workspace_id,
            "request_node_id": request_node_id,
            "source_document_id": source_document_id,
            "maintenance_kind": "distill",
            "lane_message_id": lane_message_id,
        }
        job_id = request_node_id
        self.engines.conversation.meta_sqlite.enqueue_index_job(
            job_id=job_id,
            namespace=namespace,
            entity_kind="maintenance_job",
            entity_id=source_document_id,
            index_kind="maintenance_job",
            op="UPSERT",
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        return job_id

    def _enqueue_projection_job(
        self,
        *,
        request: IngestPipelineRequest,
        promoted_id: str,
        namespace: str,
    ) -> str:
        job_id = str(
            stable_id(
                "kogwistar_llm_wiki.projection_request",
                request.workspace_id,
                promoted_id,
            )
        )
        payload = {
            "workspace_id": request.workspace_id,
            "promoted_entity_id": promoted_id,
            "promotion_mode": request.promotion_mode,
        }
        self.engines.conversation.meta_sqlite.enqueue_index_job(
            job_id=job_id,
            namespace=namespace,
            entity_kind="projection_request",
            entity_id=promoted_id,
            index_kind="projection_request",
            op="UPSERT",
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        return job_id

    def build_projection_snapshot(self, workspace_id: str) -> ProjectionSnapshot:
        return self.projection.build_projection_snapshot(workspace_id=workspace_id)

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
            id=node_id,
            label=label,
            type="entity",
            summary=summary,
            doc_id=source_document_id,
            mentions=[{
                "spans": [span.model_dump(field_mode="backend")]
            }],
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
