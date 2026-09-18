"""Immutable source revisions and durable source-parse lifecycle operations."""

from __future__ import annotations

import hashlib
import json
import uuid

from kogwistar.engine_core.models import Document
from kogwistar.id_provider import stable_id

from ..debug_run import now_ms
from ..maintenance.maintenance_guards import (
    SourceRevision,
    build_source_revision,
    readiness_id,
    source_digest,
    source_revision_document_id,
)
from ..models import IngestPipelineRequest
from ..parsing.parse_session_store import ParseSessionStore, ParseSessionStoreConflict
from ..parsing.parse_views import (
    ParseFrontierItem,
    ParseGeneration,
    ParseSessionPhase,
    ParseSessionState,
    SourceRegion,
    frontier_id,
    generation_id,
    parse_session_id,
)
from ..utils import _temporary_namespace


def _metadata_digest_value(digest: dict[str, object] | None) -> str | None:
    if digest is None:
        return None
    return json.dumps(digest, sort_keys=True, separators=(",", ":"))


class SourceLifecycleMixin:
    """Keep immutable source and durable parser lifecycle out of orchestration."""

    def _source_document_id(self, request: IngestPipelineRequest) -> str:
        return str(
            stable_id(
                "kogwistar_llm_wiki.source_document",
                request.workspace_id,
                request.source_uri,
            )
        )

    @staticmethod
    def _operation_mode(request: IngestPipelineRequest) -> str:
        operation_mode = str(
            getattr(request, "operation_mode", "parse_first") or "parse_first"
        ).strip().lower()
        if operation_mode not in {"parse_first", "maintenance_first", "hybrid"}:
            raise ValueError("operation_mode must be one of: parse_first, maintenance_first, hybrid")
        return operation_mode

    @staticmethod
    def _maintenance_kind_for_operation_mode(operation_mode: str) -> str:
        if operation_mode == "maintenance_first":
            return "document_seed_graph"
        if operation_mode == "hybrid":
            return "document_expand_parse_children"
        return "distill"

    @staticmethod
    def _durable_parse_limits(request: IngestPipelineRequest) -> dict[str, int | float | None]:
        """Validate persisted parser bounds before any worker can consume them."""

        raw = dict(request.parse_limits or {})
        integer_defaults = {
            "max_depth": 10,
            "max_frontier_items": 1,
            "max_parser_calls": 1000,
            "max_region_chars": 16_384,
        }
        unknown = set(raw) - set(integer_defaults) - {"token_budget", "wall_time_seconds"}
        if unknown:
            raise ValueError("unsupported parse_limits: " + ", ".join(sorted(unknown)))
        limits: dict[str, int | float | None] = {}
        for name, default in integer_defaults.items():
            value = raw.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"parse_limits.{name} must be a positive integer")
            limits[name] = value
        token_budget = raw.get("token_budget")
        if token_budget is not None:
            if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
                raise ValueError("parse_limits.token_budget must be a positive integer")
            limits["token_budget"] = token_budget
        wall_time_seconds = raw.get("wall_time_seconds")
        if wall_time_seconds is not None:
            if (
                isinstance(wall_time_seconds, bool)
                or not isinstance(wall_time_seconds, (int, float))
                or wall_time_seconds <= 0
            ):
                raise ValueError("parse_limits.wall_time_seconds must be positive")
            limits["wall_time_seconds"] = float(wall_time_seconds)
        return limits

    def source_revision(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> SourceRevision:
        key = (request.workspace_id, source_document_id)
        revision = self._source_revisions.get(key)
        if revision is None:
            candidate = build_source_revision(
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                raw_text=request.raw_text,
            )
            source_namespace = self.namespaces_for(request.workspace_id).source_space
            with _temporary_namespace(self.engines.kg, source_namespace):
                persisted_nodes = self.engines.kg.read.get_nodes(
                    where={
                        "$and": [
                            {"artifact_kind": "source_revision"},
                            {"source_document_id": source_document_id},
                            {"source_digest": candidate.source_digest},
                        ]
                    },
                    limit=10_000,
                )
            if persisted_nodes:
                latest = max(
                    persisted_nodes,
                    key=lambda node: int(
                        (getattr(node, "metadata", {}) or {}).get("created_at_ms") or 0
                    ),
                )
                metadata = dict(getattr(latest, "metadata", {}) or {})
                revision = SourceRevision(
                    source_document_id=source_document_id,
                    revision_id=str(metadata.get("source_revision_id") or latest.id),
                    source_digest=str(metadata.get("source_digest") or candidate.source_digest),
                    revision_document_id=str(
                        metadata.get("revision_document_id")
                        or source_revision_document_id(
                            workspace_id=request.workspace_id,
                            source_document_id=source_document_id,
                            revision_id=str(metadata.get("source_revision_id") or latest.id),
                        )
                    ),
                )
            else:
                revision = candidate
            self._source_revisions[key] = revision
        return revision

    def begin_source_revision(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> SourceRevision:
        """Start an immutable ingestion attempt for a source document."""
        digest = source_digest(request.raw_text)
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        with _temporary_namespace(self.engines.kg, source_namespace):
            persisted_nodes = self.engines.kg.read.get_nodes(
                where={
                    "$and": [
                        {"artifact_kind": "source_revision"},
                        {"source_document_id": source_document_id},
                        {"source_digest": digest},
                    ],
                },
                limit=10_000,
            )
        if persisted_nodes:
            latest = max(
                persisted_nodes,
                key=lambda node: int(
                    (getattr(node, "metadata", {}) or {}).get("created_at_ms") or 0
                ),
            )
            metadata = dict(getattr(latest, "metadata", {}) or {})
            revision = SourceRevision(
                source_document_id=source_document_id,
                revision_id=str(metadata.get("source_revision_id") or latest.id),
                source_digest=str(metadata.get("source_digest") or digest),
                revision_document_id=str(
                    metadata.get("revision_document_id")
                    or source_revision_document_id(
                        workspace_id=request.workspace_id,
                        source_document_id=source_document_id,
                        revision_id=str(metadata.get("source_revision_id") or latest.id),
                    )
                ),
            )
            self._source_revisions[(request.workspace_id, source_document_id)] = revision
            return revision
        revision = build_source_revision(
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            raw_text=request.raw_text,
            attempt_id=str(uuid.uuid4()),
        )
        self._source_revisions[(request.workspace_id, source_document_id)] = revision
        return revision

    def record_source_readiness(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        stage: str,
    ) -> str:
        revision = self.source_revision(request=request, source_document_id=source_document_id)
        ns = self.namespaces_for(request.workspace_id)
        node_id = readiness_id(
            source_document_id=source_document_id,
            revision_id=revision.revision_id,
            stage=stage,
        )
        node = self._artifact_node(
            request=request,
            source_document_id=revision.revision_document_id or source_document_id,
            namespace=ns.source_space,
            node_id=node_id,
            artifact_kind="source_readiness",
            lane="background",
            visibility="internal",
            label=f"Source Readiness: {stage}",
            summary=f"Source revision is ready for {stage}.",
            extra_metadata={
                "source_document_id": revision.source_document_id,
                "source_revision_document_id": revision.revision_document_id,
                "source_revision_id": revision.revision_id,
                "source_digest": revision.source_digest,
                "readiness_stage": stage,
                "graph_status": "ready",
                "created_at_ms": now_ms(),
            },
        )
        with _temporary_namespace(self.engines.kg, ns.source_space):
            if not self.engines.kg.read.node_exists(ids=[node_id]):
                self.engines.kg.write.add_node(node)
        return node_id

    def initialize_durable_parse_session(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        revision_document_id: str,
        revision: SourceRevision | None = None,
        max_depth: int = 10,
        max_frontier_items: int = 1,
        max_parser_calls: int = 1000,
        max_region_chars: int = 16_384,
        token_budget: int | None = None,
        wall_time_seconds: float | None = None,
        parser_profile: str | None = None,
        parser_version: str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
        initial_region: SourceRegion | None = None,
        session_id_override: str | None = None,
    ) -> ParseSessionState:
        """Create idempotent app-owned state for restartable layered parsing."""

        revision = revision or self.source_revision(
            request=request,
            source_document_id=source_document_id,
        )
        resolved_profile = parser_profile or request.parser_lane
        resolved_parser_version = parser_version or "llm-wiki-layered-contract-v1"
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if max_frontier_items < 1:
            raise ValueError("max_frontier_items must be positive")
        if max_parser_calls < 1:
            raise ValueError("max_parser_calls must be positive")
        if max_region_chars < 1:
            raise ValueError("max_region_chars must be positive")
        if token_budget is not None and token_budget < 1:
            raise ValueError("token_budget must be positive")
        if wall_time_seconds is not None and wall_time_seconds <= 0:
            raise ValueError("wall_time_seconds must be positive")
        session_id = session_id_override or parse_session_id(
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            source_revision_id=revision.revision_id,
            parser_profile=resolved_profile,
        )
        generation = generation_id(
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            source_revision_id=revision.revision_id,
            parser_profile=resolved_profile,
            derivation_id=session_id,
        )
        store = ParseSessionStore(
            self.engines.conversation.meta_sqlite,
            workspace_id=request.workspace_id,
        )
        existing = store.get(session_id)
        if existing is not None:
            return existing[0]
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        with _temporary_namespace(self.engines.kg, source_namespace):
            revision_document = self.engines.kg.read.get_document(revision_document_id)
        end_char = max(len(str(revision_document.content or "")), 1)
        region = initial_region or SourceRegion(
            source_document_id=revision_document_id,
            start_char=0,
            end_char=end_char,
        )
        if region.source_document_id != revision_document_id or region.end_char > end_char:
            raise ValueError("initial parse region must be within the immutable revision document")
        frontier = ParseFrontierItem(
            frontier_id=frontier_id(session_id=session_id, region=region, ordinal=0),
            session_id=session_id,
            generation_id=generation,
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            source_revision_id=revision.revision_id,
            revision_document_id=revision_document_id,
            region=region,
            depth=0,
            ordinal=0,
        )
        session = ParseSessionState(
            session_id=session_id,
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            source_revision_id=revision.revision_id,
            source_digest=revision.source_digest,
            revision_document_id=revision_document_id,
            generation_id=generation,
            phase=ParseSessionPhase.SEEDED,
            frontier_ids=(frontier.frontier_id,),
            max_depth=max_depth,
            max_frontier_items=max_frontier_items,
            max_parser_calls=max_parser_calls,
            max_region_chars=max_region_chars,
            token_budget=token_budget,
            wall_time_seconds=wall_time_seconds,
            parser_state={
                "schema_version": 1,
                "source_revision_document_id": revision_document_id,
                "source_uri": request.source_uri,
                "title": request.title,
                "source_format": request.source_format,
                "parser_mode": request.parser_mode,
                "parser_lane": request.parser_lane,
                "parser_profile": resolved_profile,
                "parser_version": resolved_parser_version,
                "model_version": model_version,
                "prompt_version": prompt_version,
                "promotion_mode": request.promotion_mode,
                "llm_provider": request.llm_provider,
                "llm_model": request.llm_model,
            },
        )
        try:
            store.save(session, [frontier], expected_version=None)
        except ParseSessionStoreConflict:
            existing = store.get(session_id)
            if existing is None:
                raise
            return existing[0]
        generation_evidence = ParseGeneration(
            generation_id=generation,
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            source_revision_id=revision.revision_id,
            source_digest=revision.source_digest,
            revision_document_id=revision_document_id,
            parser_profile=resolved_profile,
            parser_version=resolved_parser_version,
            llm_provider=request.llm_provider,
            llm_model=request.llm_model,
            model_version=model_version,
            prompt_version=prompt_version,
            status="seeded",
        )
        node = self._artifact_node(
            request=request,
            source_document_id=revision_document_id,
            namespace=self.namespaces_for(request.workspace_id).source_space,
            node_id=generation,
            artifact_kind="parse_generation",
            lane="background",
            visibility="internal",
            label=f"Parse Generation: {request.title}",
            summary="Immutable coarse parse generation evidence.",
            extra_metadata={
                **generation_evidence.model_dump(mode="json"),
                "parse_session_id": session_id,
                "frontier_ids": [frontier.frontier_id],
            },
        )
        with _temporary_namespace(self.engines.kg, self.namespaces_for(request.workspace_id).source_space):
            if not self.engines.kg.read.node_exists(ids=[generation]):
                self.engines.kg.write.add_node(node)
        self.record_source_readiness(
            request=request,
            source_document_id=source_document_id,
            stage="parse_seeded",
        )
        return session

    def register_source(self, *, request: IngestPipelineRequest, source_document_id: str, namespace: str) -> None:
        revision = self.begin_source_revision(request=request, source_document_id=source_document_id)
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        revision_document_id = revision.revision_document_id or source_revision_document_id(
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            revision_id=revision.revision_id,
        )
        source_metadata = {
            "workspace_id": request.workspace_id,
            "graph_space": "source",
            "logical_source_document_id": source_document_id,
            "source_revision_id": revision.revision_id,
            "source_digest": revision.source_digest,
            "revision_document_id": revision_document_id,
            "source_uri": request.source_uri,
            "title": request.title,
            "source_format": request.source_format,
            "operation_mode": self._operation_mode(request),
            "parser_mode": request.parser_mode,
            "parser_lane": request.parser_lane,
            "promotion_mode": request.promotion_mode,
            "llm_provider": request.llm_provider,
            "llm_model": request.llm_model,
            "parse_limits": dict(request.parse_limits or {}),
            "provenance_policy": request.provenance_policy,
            "provenance": _metadata_digest_value(dict(request.provenance or {})),
        }
        revision_document = Document(
            id=revision_document_id,
            content=request.raw_text,
            type="text",
            metadata=dict(source_metadata),
        )
        compatibility_metadata = dict(source_metadata)
        compatibility_metadata["legacy_namespace"] = namespace
        compatibility_document = Document(
            id=revision_document_id,
            content=request.raw_text,
            type="text",
            metadata=compatibility_metadata,
        )

        with _temporary_namespace(self.engines.kg, source_namespace):
            if not self._document_exists(self.engines.kg, revision_document_id):
                self.engines.kg.write.add_document(revision_document)
            if not self._document_exists(self.engines.kg, source_document_id):
                self.engines.kg.write.add_document(
                    Document(
                        id=source_document_id,
                        content=request.raw_text,
                        type="text",
                        metadata={**source_metadata, "legacy_alias": True},
                    )
                )
        with _temporary_namespace(self.engines.conversation, namespace):
            if not self._document_exists(self.engines.conversation, revision_document_id):
                self.engines.conversation.write.add_document(compatibility_document)
            if not self._document_exists(self.engines.conversation, source_document_id):
                self.engines.conversation.write.add_document(
                    Document(
                        id=source_document_id,
                        content=request.raw_text,
                        type="text",
                        metadata={**compatibility_metadata, "legacy_alias": True},
                    )
                )
        revision_node = self._artifact_node(
            request=request,
            source_document_id=revision_document_id,
            namespace=source_namespace,
            node_id=revision.revision_id,
            artifact_kind="source_revision",
            lane="background",
            visibility="internal",
            label=f"Source Revision: {request.title}",
            summary="Authoritative source revision fence for maintenance jobs.",
            extra_metadata={
                "source_document_id": source_document_id,
                "source_revision_document_id": revision_document_id,
                "source_revision_id": revision.revision_id,
                "source_digest": revision.source_digest,
                "revision_document_id": revision_document_id,
                "revision_status": "current",
                "source_raw_text": request.raw_text,
                "source_format": request.source_format,
                "operation_mode": self._operation_mode(request),
                "parser_lane": request.parser_lane,
                "promotion_mode": request.promotion_mode,
                "llm_provider": request.llm_provider,
                "llm_model": request.llm_model,
                "provenance_policy": request.provenance_policy,
                "provenance": _metadata_digest_value(dict(request.provenance or {})),
                "created_at_ms": now_ms(),
            },
        )
        with _temporary_namespace(self.engines.kg, source_namespace):
            if not self.engines.kg.read.node_exists(ids=[revision.revision_id]):
                self.engines.kg.write.add_node(revision_node)
        self.record_source_readiness(
            request=request,
            source_document_id=source_document_id,
            stage="source_registered",
        )

    def seed_source_map(self, *, request: IngestPipelineRequest, source_document_id: str, namespace: str) -> str:
        revision = self.source_revision(request=request, source_document_id=source_document_id)
        revision_document_id = revision.revision_document_id or source_revision_document_id(
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            revision_id=revision.revision_id,
        )
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        source_map_digest = hashlib.sha256((request.raw_text or "").encode("utf-8")).hexdigest()
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.source_map_seed",
                request.workspace_id,
                source_document_id,
                source_map_digest,
            )
        )
        seed_metadata = {
            "source_document_id": self._source_document_id(request),
            "source_revision_document_id": revision_document_id,
            "source_revision_id": revision.revision_id,
            "graph_space": "source",
            "operation_mode": self._operation_mode(request),
            "graph_status": "seeded",
            "source_map_digest": source_map_digest,
            "source_map_kind": "single_text_span",
            "source_span_count": 1 if request.raw_text else 0,
        }
        source_seed = self._artifact_node(
            request=request,
            source_document_id=revision_document_id,
            namespace=source_namespace,
            node_id=node_id,
            artifact_kind="source_map_seed",
            lane="background",
            visibility="internal",
            label=f"Source Map Seed: {request.title}",
            summary=f"Seed source map for {request.title}",
            extra_metadata=seed_metadata,
        )
        compatibility_seed = self._artifact_node(
            request=request,
            source_document_id=revision_document_id,
            namespace=namespace,
            node_id=node_id,
            artifact_kind="source_map_seed",
            lane="background",
            visibility="internal",
            label=f"Source Map Seed: {request.title}",
            summary=f"Seed source map for {request.title}",
            extra_metadata={**seed_metadata, "legacy_namespace": namespace},
        )
        with _temporary_namespace(self.engines.kg, source_namespace):
            if not self.engines.kg.read.node_exists(ids=[node_id]):
                self.engines.kg.write.add_node(source_seed)
        with _temporary_namespace(self.engines.conversation, namespace):
            if not self.engines.conversation.read.node_exists(ids=[node_id]):
                self.engines.conversation.write.add_node(compatibility_seed)
        self.record_source_readiness(
            request=request,
            source_document_id=source_document_id,
            stage="source_map_seeded",
        )
        return node_id
