"""App-owned bounded graph exploration over existing Kogwistar reads.

The lens is a disposable presentation projection.  It never becomes graph
truth and it deliberately keeps Kogwistar nodes, edges, spans, and metadata in
their existing shapes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from math import isnan
from typing import Literal, Protocol, cast

from kogwistar.engine_core.models import Edge, Node
from kogwistar.json_types import JsonValue

from ..configuration.workspace import GraphSpace, WorkspaceNamespaces
from ..models import NamespaceEngines
from ..utils import _temporary_namespace
from .contracts import Clock
from .query import GraphSpaceQueryResult, GraphSpaceQueryService

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)
logger = logging.getLogger(__name__)
RetrievalMode = Literal["auto", "graph", "semantic", "flat"]
GraphEntity = Node | Edge


class _ScoredNode(Protocol):
    node: Node
    similarity: float | None


@dataclass(frozen=True, slots=True)
class SemanticLensRequest:
    workspace_id: str
    graph_spaces: tuple[GraphSpace | str, ...] = (GraphSpace.CURATED_KG,)
    query_text: str = ""
    semantic_retrieval: bool = False
    retrieval_mode: RetrievalMode = "auto"
    retrieval_required: bool = False
    similarity_threshold: float | None = None
    source_evidence_required: bool = False
    explicit_anchor_ids: tuple[str, ...] = ()
    hop_limit: int = 1
    max_nodes: int = 40
    max_edges: int = 80
    max_hyperedges: int = 12
    pinned_node_ids: tuple[str, ...] = ()
    source_watermark: str | int | None = None
    include_tombstones: bool = False

    def __post_init__(self) -> None:
        if not str(self.workspace_id).strip():
            raise ValueError("workspace_id must not be empty")
        if not self.graph_spaces:
            raise ValueError("graph_spaces must not be empty")
        if self.retrieval_mode not in {"auto", "graph", "semantic", "flat"}:
            raise ValueError("retrieval_mode must be auto, graph, semantic, or flat")
        if self.retrieval_mode == "graph" and self.similarity_threshold is not None:
            raise ValueError("similarity_threshold requires semantic or flat retrieval_mode")
        if self.retrieval_mode in {"graph", "semantic"} and self.source_evidence_required:
            raise ValueError("source_evidence_required requires flat retrieval_mode")
        if self.similarity_threshold is not None:
            threshold = float(self.similarity_threshold)
            if isnan(threshold):
                raise ValueError("similarity_threshold must not be NaN")
        if not 0 <= self.hop_limit <= 8:
            raise ValueError("hop_limit must be between 0 and 8")
        if self.max_nodes < 1 or self.max_edges < 0 or self.max_hyperedges < 0:
            raise ValueError("display budgets must be non-negative")
        if len(set(self.pinned_node_ids)) > self.max_nodes:
            raise ValueError("max_nodes must retain all pinned nodes")


@dataclass(frozen=True, slots=True)
class SelectionExplanation:
    node_id: str
    reason: str
    score: float
    matched_terms: tuple[str, ...] = ()
    distance: int | None = None


@dataclass(frozen=True, slots=True)
class LensNode:
    id: str
    graph_space: str
    namespace: str
    label: str
    node_type: str
    entity_revision: str | int | None
    metadata: dict[str, object]
    grounding: tuple[dict[str, object], ...] = ()
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LensEdge:
    id: str
    graph_space: str
    namespace: str
    source_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    relation: str
    entity_revision: str | int | None
    metadata: dict[str, object]
    grounding: tuple[dict[str, object], ...] = ()
    payload: dict[str, object] = field(default_factory=dict)

    @property
    def is_hyperedge(self) -> bool:
        return len(self.source_ids) + len(self.target_ids) > 2


@dataclass(frozen=True, slots=True)
class LensParticipation:
    edge_id: str
    node_id: str
    role: str


@dataclass(frozen=True, slots=True)
class RetrievalStatus:
    mode: str
    semantic_available: bool
    backend: str | None
    metric: str | None
    degraded_reason: str | None = None
    score_kind: str = "similarity"


@dataclass(frozen=True, slots=True)
class SemanticLensSnapshot:
    lens_id: str
    workspace_id: str
    source_watermark: str | int | None
    projected_at_ms: int
    completeness: str
    nodes: tuple[LensNode, ...]
    edges: tuple[LensEdge, ...]
    hyperedges: tuple[LensEdge, ...]
    participations: tuple[LensParticipation, ...]
    anchor_explanations: tuple[SelectionExplanation, ...]
    selection_explanations: tuple[SelectionExplanation, ...]
    omitted_summary: dict[str, int]
    query_timing_ms: int
    retrieval: RetrievalStatus = field(
        default_factory=lambda: RetrievalStatus("graph", False, None, None, None, "similarity")
    )
    flat_hits: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], _jsonable(asdict(self)))


@dataclass(frozen=True, slots=True)
class InvestigationOutcome:
    outcome: str
    session_id: str
    lens_id: str
    source_watermark: str | int | None
    cited_entity_ids: tuple[str, ...] = ()
    proposal: dict[str, object] | None = None
    insufficiency_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], _jsonable(asdict(self)))


@dataclass(frozen=True, slots=True)
class ProposalValidation:
    accepted: bool
    requires_confirmation: bool
    reason: str
    target_ids: tuple[str, ...] = ()


def validate_edit_proposal(
    snapshot: SemanticLensSnapshot,
    proposal: Mapping[str, object],
) -> ProposalValidation:
    """Validate an app proposal before it can reach an existing mutation path."""
    if str(proposal.get("lens_id") or "") != snapshot.lens_id:
        return ProposalValidation(False, True, "stale_lens_id")
    if proposal.get("source_watermark") != snapshot.source_watermark:
        return ProposalValidation(False, True, "stale_source_watermark")
    target_ids = _string_ids(proposal.get("target_ids"))
    visible_ids = {node.id for node in snapshot.nodes}
    visible_ids.update(edge.id for edge in (*snapshot.edges, *snapshot.hyperedges))
    missing = [node_id for node_id in target_ids if node_id not in visible_ids]
    if missing:
        return ProposalValidation(False, True, "target_not_in_scoped_lens")
    evidence_ids = _string_ids(proposal.get("evidence_ids"))
    if not evidence_ids and proposal.get("operation") not in {None, "no_change"}:
        return ProposalValidation(False, True, "grounding_evidence_required")
    missing_evidence = [entity_id for entity_id in evidence_ids if entity_id not in visible_ids]
    if missing_evidence:
        return ProposalValidation(False, True, "evidence_not_in_scoped_lens")
    expected_revisions = proposal.get("expected_revisions")
    if isinstance(expected_revisions, Mapping):
        revisions = {node.id: node.entity_revision for node in snapshot.nodes}
        revisions.update({edge.id: edge.entity_revision for edge in (*snapshot.edges, *snapshot.hyperedges)})
        for node_id, expected in expected_revisions.items():
            if str(node_id) not in revisions or revisions[str(node_id)] != expected:
                return ProposalValidation(False, True, "stale_entity_revision")
    return ProposalValidation(True, True, "ready_for_explicit_confirmation", target_ids)


class SemanticLensService:
    """Resolve bounded lenses without inventing storage semantics."""

    __slots__ = ("_clock_ms", "engines", "query_service")

    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        query_service: GraphSpaceQueryService | None = None,
        clock_ms: Clock | None = None,
    ) -> None:
        self.engines = engines
        self.query_service = query_service or GraphSpaceQueryService(engines)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def resolve(self, request: SemanticLensRequest) -> SemanticLensSnapshot:
        started = self._clock_ms()
        mode = _effective_retrieval_mode(request)
        if mode == "flat":
            vector_results, status = self._vector_results(request)
            if status.degraded_reason and request.retrieval_required:
                raise ValueError(status.degraded_reason)
            all_flat_hits = tuple(
                self._flat_hit(
                    item.node,
                    item.graph_space,
                    item.namespace,
                    score,
                    workspace_id=request.workspace_id,
                )
                for item, score in vector_results
            )
            if request.source_evidence_required:
                all_flat_hits = tuple(
                    hit
                    for hit in all_flat_hits
                    if _verified_source_evidence(hit)
                )
            flat_hits = all_flat_hits[: request.max_nodes]
            if request.source_evidence_required and not flat_hits and vector_results:
                status = RetrievalStatus(
                    status.mode,
                    status.semantic_available,
                    status.backend,
                    status.metric,
                    "no_verified_source_evidence",
                    status.score_kind,
                )
            identity = {"request": _request_key(request), "flat": flat_hits}
            lens_id = "lens:" + hashlib.sha256(_stable_json(identity).encode()).hexdigest()[:24]
            return SemanticLensSnapshot(
                lens_id=lens_id,
                workspace_id=request.workspace_id,
                source_watermark=request.source_watermark,
                projected_at_ms=self._clock_ms(),
                completeness="exhausted" if not status.degraded_reason else "bounded",
                nodes=(), edges=(), hyperedges=(), participations=(),
                anchor_explanations=(), selection_explanations=(),
                omitted_summary={"candidate_nodes": 0, "candidate_edges": 0, "candidate_hyperedges": 0},
                query_timing_ms=max(0, self._clock_ms() - started),
                retrieval=status,
                flat_hits=flat_hits,
            )
        results = self.query_service.get_nodes(
            workspace_id=request.workspace_id,
            graph_spaces=list(request.graph_spaces),
            resolve_mode="include_tombstone" if request.include_tombstones else "pointer_only",
        )
        retrieval = self._graph_status(request)
        semantic_anchor_ids: tuple[str, ...] = ()
        if mode == "semantic":
            results, retrieval, semantic_anchor_ids = self._merge_vector_results(request, results)
            if retrieval.degraded_reason and request.retrieval_required:
                raise ValueError(retrieval.degraded_reason)
        candidates = [_lens_node(result) for result in results if _node_id(result.node)]
        by_id = {node.id: node for node in candidates}
        scores = {
            node.id: _score_node(node, request.query_text, request.pinned_node_ids)
            for node in candidates
        }
        matched = {node_id: terms for node_id, (_, terms) in scores.items() if terms}
        anchors = self._anchors(request, candidates, scores, semantic_anchor_ids)
        edges = self._read_edges(request, by_id)
        distance = self._distances(anchors, edges, request.hop_limit)
        retained = self._retain_nodes(request, candidates, scores, distance, anchors)
        retained_ids = {node.id for node in retained}
        visible_edges = tuple(
            edge
            for edge in edges
            if set(edge.source_ids + edge.target_ids) & retained_ids
            and set(edge.source_ids + edge.target_ids) <= retained_ids
        )[: request.max_edges]
        hyperedges = tuple(edge for edge in visible_edges if edge.is_hyperedge)[: request.max_hyperedges]
        participations = tuple(
            participation
            for edge in hyperedges
            for participation in _participations(edge)
        )
        explanations = tuple(
            SelectionExplanation(
                node_id=node.id,
                reason=(
                    "pinned"
                    if node.id in request.pinned_node_ids
                    else "query_match"
                    if matched.get(node.id)
                    else "traversed_neighbor"
                ),
                score=round(scores[node.id][0], 6),
                matched_terms=tuple(matched.get(node.id, ())),
                distance=distance.get(node.id),
            )
            for node in retained
        )
        anchor_explanations = tuple(item for item in explanations if item.reason in {"pinned", "query_match"})
        omitted = {
            "candidate_nodes": max(0, len(candidates) - len(retained)),
            "candidate_edges": max(0, len(edges) - len(visible_edges)),
            "candidate_hyperedges": max(0, sum(edge.is_hyperedge for edge in edges) - len(hyperedges)),
        }
        completeness = "exhausted" if not any(omitted.values()) else "partial_due_to_budget"
        if not retained:
            completeness = "bounded"
        identity = {
            "request": _request_key(request),
            "nodes": [node.id for node in retained],
            "edges": [edge.id for edge in visible_edges],
            "source_watermark": request.source_watermark,
        }
        lens_id = "lens:" + hashlib.sha256(_stable_json(identity).encode()).hexdigest()[:24]
        return SemanticLensSnapshot(
            lens_id=lens_id,
            workspace_id=request.workspace_id,
            source_watermark=request.source_watermark,
            projected_at_ms=self._clock_ms(),
            completeness=completeness,
            nodes=tuple(retained),
            edges=visible_edges,
            hyperedges=hyperedges,
            participations=participations,
            anchor_explanations=anchor_explanations,
            selection_explanations=explanations,
            omitted_summary=omitted,
            query_timing_ms=max(0, self._clock_ms() - started),
            retrieval=retrieval,
        )

    def _merge_vector_results(
        self,
        request: SemanticLensRequest,
        results: Sequence[GraphSpaceQueryResult],
    ) -> tuple[list[GraphSpaceQueryResult], RetrievalStatus, tuple[str, ...]]:
        """Merge vector candidates while retaining an explicit degradation status."""
        vector_results, status = self._vector_results(request)
        merged = list(results)
        seen = {str(getattr(item.node, "id", "") or "") for item in merged}
        for item, _score in vector_results:
            node_id = str(getattr(item.node, "id", "") or "")
            if node_id and node_id not in seen:
                seen.add(node_id)
                merged.append(item)
        return merged, status, tuple(
            str(getattr(item.node, "id", "") or "")
            for item, _score in vector_results
            if str(getattr(item.node, "id", "") or "")
        )

    def _vector_results(
        self, request: SemanticLensRequest,
    ) -> tuple[list[tuple[GraphSpaceQueryResult, float | None]], RetrievalStatus]:
        """Run the core scored vector boundary for each requested graph space."""
        ns = WorkspaceNamespaces(request.workspace_id)
        vector_results: list[tuple[GraphSpaceQueryResult, float | None]] = []
        reasons: list[str] = []
        completed = False
        backend = self._backend_name()
        metric = self._metric()
        if not request.query_text.strip():
            return [], RetrievalStatus(_effective_retrieval_mode(request), False, backend, metric, "query_text_required")
        as_of = datetime.now(UTC)
        graph_spaces = tuple(dict.fromkeys(_normalize_space(space) for space in request.graph_spaces))
        for graph_space in graph_spaces:
            namespace = _namespace_for(ns, graph_space)
            if namespace is None:
                continue
            try:
                with _temporary_namespace(self.engines.kg, namespace):
                    search_scored = getattr(
                        self.engines.kg.read, "search_nodes_as_of_scored", None
                    )
                    search = search_scored or self.engines.kg.read.search_nodes_as_of
                    search_kwargs = {
                        "query": request.query_text,
                        "as_of_ts": as_of,
                        "where": {
                            "workspace_id": request.workspace_id,
                            "graph_space": graph_space,
                        },
                        "n_results": (
                            max(request.max_nodes * 4, 20)
                            if request.similarity_threshold is not None
                            else (
                                request.max_nodes
                                if _effective_retrieval_mode(request) == "flat"
                                else max(request.max_nodes * 2, 20)
                            )
                        ),
                        "include": ["documents", "metadatas"],
                        "follow_redirects": not request.include_tombstones,
                    }
                    if search_scored is not None:
                        search_kwargs["similarity_threshold"] = request.similarity_threshold
                    scored = search(**search_kwargs)
                    if search_scored is None:
                        if request.similarity_threshold is not None:
                            reasons.append("Kogwistar scored search is required for similarity_threshold")
                            continue
                        scored = [(node, None) for node in scored]
                    completed = True
            except (AttributeError, NotImplementedError, TypeError, ValueError, RuntimeError) as exc:
                reasons.append(f"{graph_space}: {exc}")
                continue
            scored_items = cast(
                Sequence[tuple[Node, float | None] | _ScoredNode],
                scored,
            )
            for hit in scored_items:
                if isinstance(hit, tuple):
                    node, score = hit
                else:
                    node = hit.node
                    score = hit.similarity
                if request.similarity_threshold is not None:
                    if score is None:
                        reasons.append(
                            "similarity_threshold requires normalized similarity scores"
                        )
                        continue
                    if float(score) < float(request.similarity_threshold):
                        continue
                node_id = str(getattr(node, "id", "") or "")
                if not node_id:
                    continue
                vector_results.append((
                    GraphSpaceQueryResult(
                        node=node,
                        graph_space=graph_space,
                        namespace=namespace,
                    ), score,
                ))
        if vector_results:
            return vector_results, RetrievalStatus(_effective_retrieval_mode(request), True, backend, metric, None)
        if completed and not reasons:
            return [], RetrievalStatus(_effective_retrieval_mode(request), True, backend, metric, None)
        reason = "; ".join(reasons) or "semantic backend returned no results"
        return [], RetrievalStatus(_effective_retrieval_mode(request), False, backend, metric, reason)

    def _backend_name(self) -> str | None:
        backend = getattr(self.engines.kg, "backend", None)
        if backend is None:
            backend = getattr(self.engines.kg, "_backend", None)
        return type(backend).__name__ if backend is not None else None

    def _metric(self) -> str | None:
        profile = getattr(self.engines.kg, "embedding_profile", None)
        value = getattr(profile, "similarity_metric", None)
        return str(value) if value is not None else "cosine"

    def _graph_status(self, request: SemanticLensRequest) -> RetrievalStatus:
        return RetrievalStatus("graph", False, self._backend_name(), self._metric(), None)

    def _flat_hit(
        self,
        node: Node,
        graph_space: str,
        namespace: str,
        score: float | None,
        *,
        workspace_id: str,
    ) -> dict[str, object]:
        return {
            **_flat_hit(node, graph_space, namespace, score),
            "source_evidence": self._source_evidence(
                node, graph_space, namespace, workspace_id=workspace_id
            ),
        }

    def _source_evidence(
        self,
        node: Node,
        graph_space: str,
        namespace: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, object]:
        metadata = dict(getattr(node, "metadata", None) or {})
        grounding = _grounding(node)
        pointer = grounding[0] if grounding else {}
        source_id = (
            metadata.get("source_id")
            or metadata.get("source_document_id")
            or pointer.get("source_document_id")
            or pointer.get("source_id")
        )
        revision_id = metadata.get("source_revision_id") or pointer.get("source_revision_id")
        document_id = (
            metadata.get("source_revision_document_id")
            or metadata.get("revision_document_id")
            or pointer.get("source_revision_document_id")
            or pointer.get("doc_id")
        )
        result: dict[str, object] = {
            "status": "unresolved",
            "source_id": source_id,
            "source_revision_id": revision_id,
            "locator": pointer or None,
        }
        if not document_id or not pointer:
            result["status"] = "missing_source_locator"
            return result
        resolved_workspace_id = str(workspace_id or metadata.get("workspace_id") or "").strip()
        source_namespace = (
            WorkspaceNamespaces(resolved_workspace_id).source_space
            if resolved_workspace_id
            else namespace
        )
        result["source_namespace"] = source_namespace
        try:
            with _temporary_namespace(self.engines.kg, source_namespace):
                document = self.engines.kg.read.get_document(str(document_id))
        except (AttributeError, KeyError, LookupError, RuntimeError, ValueError) as exc:
            result["status"] = "source_revision_unresolved"
            result["reason"] = str(exc)
            return result
        document_metadata = dict(getattr(document, "metadata", None) or {})
        nested_metadata = document_metadata.get("metadata")
        if isinstance(nested_metadata, str):
            try:
                nested_metadata = json.loads(nested_metadata)
            except (TypeError, ValueError):
                nested_metadata = None
        if isinstance(nested_metadata, Mapping):
            document_metadata.update(nested_metadata)
        document_workspace_id = str(document_metadata.get("workspace_id") or "").strip()
        if (
            resolved_workspace_id
            and document_workspace_id
            and document_workspace_id != resolved_workspace_id
        ):
            result["status"] = "source_namespace_mismatch"
            return result
        document_revision_id = document_metadata.get("source_revision_id")
        if revision_id and document_revision_id and str(document_revision_id) != str(revision_id):
            result["status"] = "source_revision_mismatch"
            return result
        if revision_id is None and document_revision_id is not None:
            result["source_revision_id"] = document_revision_id
        if source_id is None or str(source_id) == str(document_id):
            result["source_id"] = (
                document_metadata.get("logical_source_document_id")
                or document_metadata.get("source_document_id")
                or document_id
            )
        content = str(getattr(document, "content", "") or "")
        try:
            start = _as_int(pointer.get("start_char", 0))
            end = _as_int(pointer.get("end_char", 0))
        except (TypeError, ValueError):
            result["status"] = "invalid_source_locator"
            return result
        excerpt = str(pointer.get("excerpt") or "")
        if start < 0 or end < start or end > len(content) or content[start:end] != excerpt:
            result["status"] = "excerpt_mismatch"
            return result
        result.update({"status": "verified", "excerpt": excerpt})
        return result

    def investigate(
        self,
        *,
        session_id: str,
        snapshot: SemanticLensSnapshot,
        cited_entity_ids: Sequence[str] = (),
        proposal: Mapping[str, object] | None = None,
        insufficiency_reason: str | None = None,
    ) -> InvestigationOutcome:
        """Return a grounded app outcome; mutation requires a separate command path."""
        cited = tuple(dict.fromkeys(str(item) for item in cited_entity_ids if str(item)))
        if proposal is None:
            outcome = "no_change"
        else:
            outcome = "proposal"
        return InvestigationOutcome(
            outcome=outcome,
            session_id=session_id,
            lens_id=snapshot.lens_id,
            source_watermark=snapshot.source_watermark,
            cited_entity_ids=cited,
            proposal=dict(proposal) if proposal is not None else None,
            insufficiency_reason=insufficiency_reason,
        )

    def _anchors(
        self,
        request: SemanticLensRequest,
        candidates: Sequence[LensNode],
        scores: Mapping[str, tuple[float, tuple[str, ...]]],
        semantic_anchor_ids: Sequence[str] = (),
    ) -> tuple[str, ...]:
        by_id = {node.id for node in candidates}
        explicit = [node_id for node_id in request.explicit_anchor_ids if node_id in by_id]
        pinned = [node_id for node_id in request.pinned_node_ids if node_id in by_id]
        semantic = [node_id for node_id in semantic_anchor_ids if node_id in by_id]
        lexical = [
            node.id
            for node in sorted(candidates, key=lambda item: (-scores[item.id][0], item.id))
            if scores[node.id][1]
        ]
        return tuple(dict.fromkeys(explicit + pinned + semantic + lexical))

    def _read_edges(self, request: SemanticLensRequest, nodes: Mapping[str, LensNode]) -> list[LensEdge]:
        ns = WorkspaceNamespaces(request.workspace_id)
        requested = {_normalize_space(space) for space in request.graph_spaces}
        edges: list[LensEdge] = []
        seen: set[str] = set()
        for graph_space in requested:
            namespace = _namespace_for(ns, graph_space)
            if namespace is None:
                continue
            started = time.monotonic()
            logger.info(
                "graph_query_edges_start workspace=%s graph_space=%s namespace=%s nodes=%s",
                request.workspace_id,
                graph_space,
                namespace,
                len(nodes),
            )
            with _temporary_namespace(self.engines.kg, namespace):
                edge_limit = max(request.max_edges * 4, len(nodes) * 8, 20)
                raw_edges = self.engines.kg.read.get_edges(
                    # The temporary namespace is the graph-space boundary;
                    # edge metadata carries workspace scope but not a
                    # duplicated graph_space field in the core projection.
                    where={"workspace_id": request.workspace_id},
                    limit=edge_limit,
                    resolve_mode="include_tombstones" if request.include_tombstones else "active_only",
                    include=["documents", "metadatas"],
                )
            logger.info(
                "graph_query_edges_complete workspace=%s graph_space=%s count=%s elapsed_ms=%s",
                request.workspace_id,
                graph_space,
                len(raw_edges),
                int((time.monotonic() - started) * 1000),
            )
            for raw in raw_edges:
                edge = _lens_edge(raw, graph_space, namespace)
                if edge.id in seen or not edge.source_ids or not edge.target_ids:
                    continue
                if not set(edge.source_ids + edge.target_ids) & set(nodes):
                    continue
                seen.add(edge.id)
                edges.append(edge)
        return sorted(edges, key=lambda item: item.id)

    def _distances(
        self,
        anchors: Sequence[str],
        edges: Sequence[LensEdge],
        hop_limit: int,
    ) -> dict[str, int]:
        adjacency: dict[str, set[str]] = {}
        for edge in edges:
            endpoints = set(edge.source_ids + edge.target_ids)
            for node_id in endpoints:
                adjacency.setdefault(node_id, set()).update(endpoints - {node_id})
        distances = {node_id: 0 for node_id in anchors}
        frontier = list(anchors)
        while frontier:
            current = frontier.pop(0)
            if distances[current] >= hop_limit:
                continue
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor not in distances:
                    distances[neighbor] = distances[current] + 1
                    frontier.append(neighbor)
        return distances

    def _retain_nodes(
        self,
        request: SemanticLensRequest,
        candidates: Sequence[LensNode],
        scores: Mapping[str, tuple[float, tuple[str, ...]]],
        distances: Mapping[str, int],
        anchors: Sequence[str],
    ) -> list[LensNode]:
        anchor_set = set(anchors)
        pinned = set(request.pinned_node_ids)
        eligible = [node for node in candidates if node.id in distances or node.id in pinned]
        eligible.sort(
            key=lambda node: (
                0 if node.id in pinned else 1,
                0 if node.id in anchor_set else 1,
                distances.get(node.id, 99),
                -scores[node.id][0],
                node.id,
            )
        )
        return eligible[: request.max_nodes]


def _lens_node(result: GraphSpaceQueryResult) -> LensNode:
    node = result.node
    payload = _model_dump(node)
    metadata = dict(getattr(node, "metadata", None) or {})
    return LensNode(
        id=_node_id(node),
        graph_space=result.graph_space,
        namespace=result.namespace,
        label=str(getattr(node, "label", "") or getattr(node, "summary", "") or ""),
        node_type=str(getattr(node, "type", "") or ""),
        entity_revision=_revision(metadata),
        metadata=metadata,
        grounding=_grounding(node),
        payload=cast(dict[str, object], payload),
    )


def _lens_edge(edge: Edge, graph_space: str, namespace: str) -> LensEdge:
    payload = _model_dump(edge)
    return LensEdge(
        id=str(getattr(edge, "id", "") or ""),
        graph_space=graph_space,
        namespace=namespace,
        source_ids=tuple(str(value) for value in (getattr(edge, "source_ids", None) or ())),
        target_ids=tuple(str(value) for value in (getattr(edge, "target_ids", None) or ())),
        relation=str(getattr(edge, "relation", "") or ""),
        entity_revision=_revision(dict(getattr(edge, "metadata", None) or {})),
        metadata=dict(getattr(edge, "metadata", None) or {}),
        grounding=_grounding(edge),
        payload=cast(dict[str, object], payload),
    )


def _grounding(node: GraphEntity) -> tuple[dict[str, object], ...]:
    refs: list[dict[str, object]] = []
    for mention in getattr(node, "mentions", None) or ():
        for span in getattr(mention, "spans", None) or ():
            refs.append(cast(dict[str, object], _model_dump(span)))
    return tuple(refs)


def _revision(metadata: Mapping[str, object]) -> str | int | None:
    for key in ("entity_revision", "revision_id", "revision"):
        value = metadata.get(key)
        if isinstance(value, (str, int)) and value != "":
            return value
    return None


def _score_node(node: LensNode, query: str, pinned: Sequence[str]) -> tuple[float, tuple[str, ...]]:
    if node.id in pinned:
        return (10.0, ())
    query_terms = set(_TOKEN_RE.findall(query.lower()))
    haystack = " ".join(
        [node.label, node.node_type, str(node.metadata.get("summary", "")), str(node.metadata.get("description", ""))]
    ).lower()
    matched = tuple(sorted(term for term in query_terms if term in set(_TOKEN_RE.findall(haystack))))
    return (float(len(matched)), matched)


def _participations(edge: LensEdge) -> Iterable[LensParticipation]:
    yield from (LensParticipation(edge.id, node_id, "source") for node_id in edge.source_ids)
    yield from (LensParticipation(edge.id, node_id, "target") for node_id in edge.target_ids)


def _node_id(node: GraphEntity) -> str:
    return str(getattr(node, "id", "") or "")


def _normalize_space(space: GraphSpace | str) -> str:
    return space.value if isinstance(space, GraphSpace) else str(space).strip().lower()


def _namespace_for(ns: WorkspaceNamespaces, graph_space: str) -> str | None:
    return {
        GraphSpace.SOURCE.value: ns.source_space,
        GraphSpace.BASE_KG.value: ns.base_kg_space,
        GraphSpace.CURATED_KG.value: ns.curated_kg_space,
        GraphSpace.WISDOM.value: ns.wisdom_space,
    }.get(graph_space)


def _request_key(request: SemanticLensRequest) -> dict[str, object]:
    data = asdict(request)
    data["graph_spaces"] = [_normalize_space(value) for value in request.graph_spaces]
    return data


def _effective_retrieval_mode(request: SemanticLensRequest) -> str:
    if request.retrieval_mode != "auto":
        return request.retrieval_mode
    if request.source_evidence_required:
        return "flat"
    if request.similarity_threshold is not None:
        return "semantic"
    return "semantic" if request.semantic_retrieval else "graph"


def _flat_hit(
    node: Node,
    graph_space: str,
    namespace: str,
    score: float | None,
) -> dict[str, object]:
    metadata = dict(getattr(node, "metadata", None) or {})
    return {
        "node_id": _node_id(node),
        "graph_space": graph_space,
        "namespace": namespace,
        "score": score,
        "source_id": metadata.get("source_id") or metadata.get("source_document_id"),
        "source_revision_id": metadata.get("source_revision_id"),
        "locator": metadata.get("locator") or metadata.get("source_pointer"),
        "metadata": metadata,
    }


def _model_dump(value: object) -> dict[str, JsonValue]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            # Kogwistar models expose ``dump_format`` while plain Pydantic
            # models expose ``mode``.  Keep this compatibility at the app
            # boundary rather than changing either model implementation.
            return cast(dict[str, JsonValue], _jsonable(dump(dump_format="json")))
        except TypeError:
            return cast(dict[str, JsonValue], _jsonable(dump(mode="json")))
    return cast(dict[str, JsonValue], _jsonable(dict(getattr(value, "__dict__", {}) or {})))


def _jsonable(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, (str, bytes)):
        return _jsonable(enum_value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError("expected an integer-compatible value")
    return int(value)


def _string_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item) for item in value)


def _verified_source_evidence(hit: Mapping[str, object]) -> bool:
    evidence = hit.get("source_evidence")
    return isinstance(evidence, Mapping) and evidence.get("status") == "verified"


def _stable_json(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "InvestigationOutcome",
    "LensEdge",
    "LensNode",
    "LensParticipation",
    "ProposalValidation",
    "RetrievalMode",
    "RetrievalStatus",
    "SelectionExplanation",
    "SemanticLensRequest",
    "SemanticLensService",
    "SemanticLensSnapshot",
    "validate_edit_proposal",
]
