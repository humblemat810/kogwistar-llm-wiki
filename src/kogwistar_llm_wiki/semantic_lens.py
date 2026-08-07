"""App-owned bounded graph exploration over existing Kogwistar reads.

The lens is a disposable presentation projection.  It never becomes graph
truth and it deliberately keeps Kogwistar nodes, edges, spans, and metadata in
their existing shapes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from .models import NamespaceEngines
from .namespaces import GraphSpace, WorkspaceNamespaces
from .query import GraphSpaceQueryResult, GraphSpaceQueryService
from .utils import _temporary_namespace

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SemanticLensRequest:
    workspace_id: str
    graph_spaces: tuple[GraphSpace | str, ...] = (GraphSpace.CURATED_KG,)
    query_text: str = ""
    semantic_retrieval: bool = False
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

    def to_dict(self) -> dict[str, object]:
        return _jsonable(asdict(self))


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
        return _jsonable(asdict(self))


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
    target_ids = tuple(str(value) for value in (proposal.get("target_ids") or ()))
    visible_ids = {node.id for node in snapshot.nodes}
    visible_ids.update(edge.id for edge in (*snapshot.edges, *snapshot.hyperedges))
    missing = [node_id for node_id in target_ids if node_id not in visible_ids]
    if missing:
        return ProposalValidation(False, True, "target_not_in_scoped_lens")
    evidence_ids = tuple(str(value) for value in (proposal.get("evidence_ids") or ()))
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

    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        query_service: GraphSpaceQueryService | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.engines = engines
        self.query_service = query_service or GraphSpaceQueryService(engines)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def resolve(self, request: SemanticLensRequest) -> SemanticLensSnapshot:
        started = self._clock_ms()
        results = self.query_service.get_nodes(
            workspace_id=request.workspace_id,
            graph_spaces=list(request.graph_spaces),
            resolve_mode="include_tombstone" if request.include_tombstones else "pointer_only",
        )
        if request.semantic_retrieval and request.query_text.strip():
            results = self._merge_vector_results(request, results)
        candidates = [_lens_node(result) for result in results if _node_id(result.node)]
        by_id = {node.id: node for node in candidates}
        scores = {
            node.id: _score_node(node, request.query_text, request.pinned_node_ids)
            for node in candidates
        }
        matched = {node_id: terms for node_id, (_, terms) in scores.items() if terms}
        anchors = self._anchors(request, candidates, scores)
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
        )

    def _merge_vector_results(
        self,
        request: SemanticLensRequest,
        results: Sequence[GraphSpaceQueryResult],
    ) -> list[GraphSpaceQueryResult]:
        """Use the installed reader vector API when explicitly requested."""
        ns = WorkspaceNamespaces(request.workspace_id)
        merged = list(results)
        seen = {str(getattr(item.node, "id", "") or "") for item in merged}
        as_of = datetime.now(timezone.utc)
        for graph_space in {_normalize_space(space) for space in request.graph_spaces}:
            namespace = _namespace_for(ns, graph_space)
            if namespace is None:
                continue
            try:
                with _temporary_namespace(self.engines.kg, namespace):
                    nodes = self.engines.kg.read.search_nodes_as_of(
                        query=request.query_text,
                        as_of_ts=as_of,
                        where={
                            "workspace_id": request.workspace_id,
                            "graph_space": graph_space,
                        },
                        n_results=max(request.max_nodes * 2, 20),
                        include=["documents", "metadatas"],
                        follow_redirects=not request.include_tombstones,
                    )
            except (AttributeError, NotImplementedError, TypeError, ValueError):
                continue
            for node in nodes:
                node_id = str(getattr(node, "id", "") or "")
                if not node_id or node_id in seen:
                    continue
                seen.add(node_id)
                merged.append(
                    GraphSpaceQueryResult(
                        node=node,
                        graph_space=graph_space,
                        namespace=namespace,
                    )
                )
        return merged

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
    ) -> tuple[str, ...]:
        by_id = {node.id for node in candidates}
        explicit = [node_id for node_id in request.explicit_anchor_ids if node_id in by_id]
        pinned = [node_id for node_id in request.pinned_node_ids if node_id in by_id]
        lexical = [
            node.id
            for node in sorted(candidates, key=lambda item: (-scores[item.id][0], item.id))
            if scores[node.id][1]
        ]
        return tuple(dict.fromkeys(explicit + pinned + lexical))

    def _read_edges(self, request: SemanticLensRequest, nodes: Mapping[str, LensNode]) -> list[LensEdge]:
        ns = WorkspaceNamespaces(request.workspace_id)
        requested = {_normalize_space(space) for space in request.graph_spaces}
        edges: list[LensEdge] = []
        seen: set[str] = set()
        for graph_space in requested:
            namespace = _namespace_for(ns, graph_space)
            if namespace is None:
                continue
            with _temporary_namespace(self.engines.kg, namespace):
                raw_edges = self.engines.kg.read.get_edges(
                    limit=max(400, len(nodes) * 8),
                    resolve_mode="include_tombstones" if request.include_tombstones else "active_only",
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
        payload=payload,
    )


def _lens_edge(edge: Any, graph_space: str, namespace: str) -> LensEdge:
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
        payload=payload,
    )


def _grounding(node: Any) -> tuple[dict[str, object], ...]:
    refs: list[dict[str, object]] = []
    for mention in getattr(node, "mentions", None) or ():
        for span in getattr(mention, "spans", None) or ():
            refs.append(_model_dump(span))
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


def _node_id(node: Any) -> str:
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


def _model_dump(value: Any) -> dict[str, object]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            # Kogwistar models expose ``dump_format`` while plain Pydantic
            # models expose ``mode``.  Keep this compatibility at the app
            # boundary rather than changing either model implementation.
            return _jsonable(dump(dump_format="json"))
        except TypeError:
            return _jsonable(dump(mode="json"))
    return _jsonable(dict(getattr(value, "__dict__", {}) or {}))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return _jsonable(value.value)
    return value


def _stable_json(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "InvestigationOutcome",
    "LensEdge",
    "LensNode",
    "LensParticipation",
    "SelectionExplanation",
    "SemanticLensRequest",
    "SemanticLensService",
    "SemanticLensSnapshot",
    "ProposalValidation",
    "validate_edit_proposal",
]
