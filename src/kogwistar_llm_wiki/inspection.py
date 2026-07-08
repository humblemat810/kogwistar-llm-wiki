from __future__ import annotations

"""Workspace graph inspection helpers for persisted llm-wiki state."""

from dataclasses import dataclass
from typing import Any, Literal

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .review_query import MaintenancePatchReport, ReviewQueryService
from .utils import _temporary_namespace

ReportScope = Literal["llm_wiki", "maintenance", "thinking", "all"]


@dataclass(frozen=True, slots=True)
class GraphSpaceCount:
    graph_space: str
    node_count: int
    edge_count: int


@dataclass(frozen=True, slots=True)
class NodeSample:
    graph_space: str
    id: str
    label: str
    node_type: str
    artifact_kind: str | None
    status: str | None


@dataclass(frozen=True, slots=True)
class EdgeSample:
    graph_space: str
    id: str
    from_node_id: str | None
    to_node_id: str | None
    relation: str | None
    label: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceQualityReport:
    workspace_id: str
    report_scope: ReportScope
    graph_quality: str
    graph_health_score: float
    graph_spaces: tuple[GraphSpaceCount, ...]
    review_artifact_counts: dict[str, int]
    maintenance_patch_report: MaintenancePatchReport
    sample_nodes: tuple[NodeSample, ...] = ()
    sample_edges: tuple[EdgeSample, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RawGraphNodeDump:
    graph_space: str
    namespace: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class RawGraphEdgeDump:
    graph_space: str
    namespace: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class GraphArtifactDump:
    workspace_id: str
    report_scope: ReportScope
    node_count: int
    edge_count: int
    raw_nodes: tuple[RawGraphNodeDump, ...] = ()
    raw_edges: tuple[RawGraphEdgeDump, ...] = ()
    sample_nodes: tuple[NodeSample, ...] = ()
    sample_edges: tuple[EdgeSample, ...] = ()


def build_workspace_quality_report(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    report_scope: ReportScope = "all",
) -> WorkspaceQualityReport:
    ns = WorkspaceNamespaces(workspace_id)
    graph_spaces = _graph_spaces_for_scope(engines, ns, report_scope)

    space_counts: list[GraphSpaceCount] = []
    for graph_space, engine, namespace in graph_spaces:
        space_counts.append(_count_graph_space(engine, namespace=namespace, graph_space=graph_space))

    review_service = ReviewQueryService(engines)
    review_artifacts = _review_artifact_counts(review_service, workspace_id=workspace_id, report_scope=report_scope)
    maintenance_patch_report = _maintenance_patch_report(
        review_service,
        workspace_id=workspace_id,
        report_scope=report_scope,
    )
    sample_nodes, sample_edges = _sample_selected_graph(engine_spaces=graph_spaces)

    graph_quality = _classify_graph_quality(space_counts, maintenance_patch_report)
    health_score = _graph_health_score(space_counts, maintenance_patch_report, review_artifacts)
    notes = _quality_notes(space_counts, maintenance_patch_report, review_artifacts, report_scope=report_scope)
    return WorkspaceQualityReport(
        workspace_id=workspace_id,
        report_scope=report_scope,
        graph_quality=graph_quality,
        graph_health_score=health_score,
        graph_spaces=tuple(space_counts),
        review_artifact_counts=review_artifacts,
        maintenance_patch_report=maintenance_patch_report,
        sample_nodes=tuple(sample_nodes),
        sample_edges=tuple(sample_edges),
        notes=tuple(notes),
    )


def build_workspace_graph_artifact_dump(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    report_scope: ReportScope = "all",
    raw_limit_per_space: int = 20,
) -> GraphArtifactDump:
    ns = WorkspaceNamespaces(workspace_id)
    graph_spaces = _graph_spaces_for_scope(engines, ns, report_scope)

    raw_nodes: list[RawGraphNodeDump] = []
    raw_edges: list[RawGraphEdgeDump] = []
    sample_nodes, sample_edges = _sample_selected_graph(
        engine_spaces=graph_spaces,
        limit_per_space=min(8, raw_limit_per_space),
    )

    for graph_space, engine, namespace in graph_spaces:
        with _temporary_namespace(engine, namespace):
            nodes = engine.read.get_nodes(limit=raw_limit_per_space)
            edges = engine.read.get_edges(limit=raw_limit_per_space)
        raw_nodes.extend(
            RawGraphNodeDump(
                graph_space=graph_space,
                namespace=namespace,
                payload=_model_dump_jsonable(node),
            )
            for node in nodes[:raw_limit_per_space]
        )
        raw_edges.extend(
            RawGraphEdgeDump(
                graph_space=graph_space,
                namespace=namespace,
                payload=_model_dump_jsonable(edge),
            )
            for edge in edges[:raw_limit_per_space]
        )

    return GraphArtifactDump(
        workspace_id=workspace_id,
        report_scope=report_scope,
        node_count=len(raw_nodes),
        edge_count=len(raw_edges),
        raw_nodes=tuple(raw_nodes),
        raw_edges=tuple(raw_edges),
        sample_nodes=tuple(sample_nodes),
        sample_edges=tuple(sample_edges),
    )


def _count_graph_space(
    engine: Any,
    *,
    namespace: str,
    graph_space: str,
) -> GraphSpaceCount:
    with _temporary_namespace(engine, namespace):
        nodes = engine.read.get_nodes(limit=10_000)
        edges = engine.read.get_edges(limit=10_000)
    return GraphSpaceCount(
        graph_space=graph_space,
        node_count=len(nodes),
        edge_count=len(edges),
    )


def _sample_selected_graph(
    *,
    engine_spaces: tuple[tuple[str, Any, str], ...],
    limit_per_space: int = 8,
) -> tuple[list[NodeSample], list[EdgeSample]]:
    sample_nodes: list[NodeSample] = []
    sample_edges: list[EdgeSample] = []
    for graph_space, engine, namespace in engine_spaces:
        with _temporary_namespace(engine, namespace):
            nodes = engine.read.get_nodes(limit=limit_per_space)
            edges = engine.read.get_edges(limit=limit_per_space)
        sample_nodes.extend(
            NodeSample(
                graph_space=graph_space,
                id=str(getattr(node, "id", "")),
                label=str(getattr(node, "label", "") or getattr(node, "summary", "") or ""),
                node_type=str(getattr(node, "type", "") or ""),
                artifact_kind=str((getattr(node, "metadata", None) or {}).get("artifact_kind") or "") or None,
                status=str((getattr(node, "metadata", None) or {}).get("patch_status") or "") or None,
            )
            for node in nodes[:limit_per_space]
        )
        sample_edges.extend(
            EdgeSample(
                graph_space=graph_space,
                id=str(getattr(edge, "id", "")),
                from_node_id=_first_value(getattr(edge, "source_ids", None), getattr(edge, "from_node_id", None)),
                to_node_id=_first_value(getattr(edge, "target_ids", None), getattr(edge, "to_node_id", None)),
                relation=str(getattr(edge, "relation", "") or "") or None,
                label=str(getattr(edge, "label", "") or "") or None,
            )
            for edge in edges[:limit_per_space]
        )
    return sample_nodes, sample_edges


def _classify_graph_quality(
    space_counts: tuple[GraphSpaceCount, ...] | list[GraphSpaceCount],
    maintenance_patch_report: MaintenancePatchReport,
) -> str:
    if not space_counts:
        return "unknown"
    total_nodes = sum(item.node_count for item in space_counts)
    total_edges = sum(item.edge_count for item in space_counts)

    if total_nodes == 0 and total_edges == 0:
        return "empty"
    if maintenance_patch_report.failed_operations > 0 or maintenance_patch_report.rejected_operations > maintenance_patch_report.applied_operations:
        return "degraded"
    if total_nodes > 0 and total_edges > 0:
        return "stable"
    return "expanding"


def _graph_health_score(
    space_counts: tuple[GraphSpaceCount, ...] | list[GraphSpaceCount],
    maintenance_patch_report: MaintenancePatchReport,
    review_artifact_counts: dict[str, int],
) -> float:
    by_space = {item.graph_space: item for item in space_counts}
    curated = by_space.get("curated_kg")
    base = by_space.get("base_kg")
    source = by_space.get("source")
    active_nodes = sum(item.node_count for item in space_counts)
    active_edges = sum(item.edge_count for item in space_counts)
    patch_total = (
        maintenance_patch_report.applied_operations
        + maintenance_patch_report.proposed_operations
        + maintenance_patch_report.rejected_operations
        + maintenance_patch_report.retracted_operations
    )
    chain_bonus = sum(review_artifact_counts.values())

    score = 0.0
    if source is not None:
        score += min(1.5, source.node_count / 20.0)
    if base is not None:
        score += min(2.0, base.node_count / 15.0)
    if curated is not None:
        score += min(3.0, curated.node_count / 10.0)
        score += min(1.5, curated.edge_count / 20.0)
    score += min(1.0, active_nodes / 100.0)
    score += min(0.5, active_edges / 200.0)
    if patch_total > 0:
        score += min(1.0, maintenance_patch_report.applied_operations / max(1, patch_total))
    if chain_bonus > 0:
        score += min(0.5, chain_bonus / 20.0)
    if maintenance_patch_report.failed_operations > 0:
        score -= min(2.0, maintenance_patch_report.failed_operations / 5.0)
    return round(max(0.0, score), 3)


def _quality_notes(
    space_counts: tuple[GraphSpaceCount, ...] | list[GraphSpaceCount],
    maintenance_patch_report: MaintenancePatchReport,
    review_artifact_counts: dict[str, int],
    *,
    report_scope: ReportScope,
) -> list[str]:
    notes: list[str] = []
    total_nodes = sum(item.node_count for item in space_counts)
    if total_nodes == 0:
        notes.append(f"{report_scope} graph slice is empty")
    if maintenance_patch_report.rejected_operations > 0:
        notes.append(f"{maintenance_patch_report.rejected_operations} patch operations were rejected")
    if maintenance_patch_report.failed_operations > 0:
        notes.append(f"{maintenance_patch_report.failed_operations} patch operations failed")
    if review_artifact_counts.get("promotion_candidate", 0) == 0 and report_scope in {"maintenance", "all"}:
        notes.append("no promotion candidates found")
    if review_artifact_counts.get("promotion_evidence_pack", 0) == 0 and report_scope in {"maintenance", "all"}:
        notes.append("no promotion evidence packs found")
    return notes


def _graph_spaces_for_scope(
    engines: NamespaceEngines,
    ns: WorkspaceNamespaces,
    report_scope: ReportScope,
) -> tuple[tuple[str, Any, str], ...]:
    all_spaces = (
        ("source", engines.kg, ns.source_space),
        ("base_kg", engines.kg, ns.base_kg_space),
        ("curated_kg", engines.kg, ns.curated_kg_space),
        ("conversation_fg", engines.conversation, ns.conv_fg),
        ("conversation_bg", engines.conversation, ns.conv_bg),
        ("workflow", engines.workflow, ns.workflow_space),
        ("wisdom", engines.wisdom, ns.wisdom_space),
    )
    if report_scope == "all":
        return all_spaces
    if report_scope == "llm_wiki":
        return all_spaces[:3]
    if report_scope == "maintenance":
        return all_spaces[4:6]
    if report_scope == "thinking":
        return (all_spaces[6],)
    return all_spaces


def _review_artifact_counts(
    review_service: ReviewQueryService,
    *,
    workspace_id: str,
    report_scope: ReportScope,
) -> dict[str, int]:
    if report_scope not in {"maintenance", "all"}:
        return {}
    return {
        "candidate_link": len(review_service.get_candidate_links(workspace_id=workspace_id)),
        "promotion_candidate": len(review_service.get_promotion_candidates(workspace_id=workspace_id)),
        "promotion_evidence_pack": len(review_service.get_promotion_evidence_packs(workspace_id=workspace_id)),
    }


def _maintenance_patch_report(
    review_service: ReviewQueryService,
    *,
    workspace_id: str,
    report_scope: ReportScope,
) -> MaintenancePatchReport:
    if report_scope not in {"maintenance", "all"}:
        return MaintenancePatchReport(patch_count=0)
    return review_service.get_maintenance_patch_report(workspace_id=workspace_id)


def _first_value(sequence: object, fallback: object) -> str | None:
    if isinstance(sequence, (list, tuple)) and sequence:
        return str(sequence[0])
    text = str(fallback or "").strip()
    return text or None


def _model_dump_jsonable(entity: object) -> dict[str, object]:
    dump = getattr(entity, "model_dump", None)
    if callable(dump):
        return dict(dump(dump_format="json"))
    return {
        key: value
        for key, value in dict(getattr(entity, "__dict__", {}) or {}).items()
        if not key.startswith("_")
    }


__all__ = [
    "GraphArtifactDump",
    "EdgeSample",
    "GraphSpaceCount",
    "NodeSample",
    "RawGraphEdgeDump",
    "RawGraphNodeDump",
    "WorkspaceQualityReport",
    "build_workspace_quality_report",
    "build_workspace_graph_artifact_dump",
]
