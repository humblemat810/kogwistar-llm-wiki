from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from kogwistar.engine_core.models import Node
from kogwistar.logical_refs import logical_ref_from_entity

from .models import NamespaceEngines
from .namespaces import GraphSpace, WorkspaceNamespaces
from .utils import _temporary_namespace


@dataclass(frozen=True, slots=True)
class GraphSpaceQueryResult:
    node: Any
    graph_space: str
    namespace: str
    reference_node: Any | None = None


def workspace_graph_spaces(*, include_wisdom: bool = False) -> list[GraphSpace]:
    spaces = [GraphSpace.SOURCE, GraphSpace.CURATED_KG]
    if include_wisdom:
        spaces.append(GraphSpace.WISDOM)
    return spaces


class GraphSpaceQueryService:
    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def get_nodes(
        self,
        *,
        workspace_id: str,
        graph_spaces: list[GraphSpace | str],
        where: Mapping[str, Any] | None = None,
        resolve_mode: str = "pointer_only",
    ) -> list[GraphSpaceQueryResult]:
        requested_spaces = [self._normalize_graph_space(space) for space in graph_spaces]
        if not requested_spaces:
            raise ValueError("graph_spaces must not be empty")

        ns = WorkspaceNamespaces(workspace_id)
        query_where = dict(where or {})
        requested_workspace_id = str(query_where.get("workspace_id") or workspace_id)
        if requested_workspace_id != workspace_id:
            raise ValueError(
                f"where.workspace_id={requested_workspace_id!r} does not match requested workspace_id={workspace_id!r}"
            )
        query_where["workspace_id"] = workspace_id

        results: list[GraphSpaceQueryResult] = []
        seen_ids: set[str] = set()
        for graph_space in requested_spaces:
            for namespace, resolved_space in self._candidate_namespaces(ns, graph_space):
                nodes = self._read_nodes(
                    engine=self._engine_for_graph_space(graph_space),
                    namespace=namespace,
                    where=query_where,
                )
                for node in nodes:
                    result = self._result_for_node(
                        node=node,
                        graph_space=graph_space,
                        resolved_space=resolved_space,
                        namespace=namespace,
                        workspace_ns=ns,
                        resolve_mode=str(resolve_mode or "pointer_only").strip().lower(),
                    )
                    if result is None:
                        continue
                    node_id = str(getattr(result.node, "id", "") or "")
                    if node_id and node_id in seen_ids:
                        continue
                    if node_id:
                        seen_ids.add(node_id)
                    results.append(result)
        return results

    def _normalize_graph_space(self, graph_space: GraphSpace | str) -> GraphSpace:
        if isinstance(graph_space, GraphSpace):
            return graph_space
        text = str(graph_space or "").strip().lower()
        return GraphSpace(text)

    def _engine_for_graph_space(self, graph_space: GraphSpace):
        if graph_space in {GraphSpace.SOURCE, GraphSpace.BASE_KG, GraphSpace.CURATED_KG}:
            return self.engines.kg
        return self.engines.kg

    def _candidate_namespaces(self, ns: WorkspaceNamespaces, graph_space: GraphSpace) -> list[tuple[str, str]]:
        if graph_space == GraphSpace.SOURCE:
            return [(ns.source_space, GraphSpace.SOURCE.value)]
        if graph_space == GraphSpace.BASE_KG:
            return [(ns.base_kg_space, GraphSpace.BASE_KG.value)]
        if graph_space == GraphSpace.CURATED_KG:
            return [(ns.curated_kg_space, GraphSpace.CURATED_KG.value)]
        return []

    def _read_nodes(self, *, engine: Any, namespace: str, where: Mapping[str, Any]) -> list[Node]:
        with _temporary_namespace(engine, namespace):
            return list(engine.read.get_nodes(where=dict(where), limit=10_000))

    def _node_matches_graph_space(self, node: Node, graph_space: GraphSpace) -> bool:
        metadata = dict(getattr(node, "metadata", None) or {})
        resolved_graph_space = str(metadata.get("graph_space") or "").strip().lower()
        artifact_kind = str(metadata.get("artifact_kind") or "").strip().lower()

        if graph_space == GraphSpace.SOURCE:
            return resolved_graph_space == GraphSpace.SOURCE.value
        if graph_space == GraphSpace.BASE_KG:
            return resolved_graph_space == GraphSpace.BASE_KG.value
        if graph_space == GraphSpace.CURATED_KG:
            return resolved_graph_space == GraphSpace.CURATED_KG.value
        return False

    def _result_for_node(
        self,
        *,
        node: Node,
        graph_space: GraphSpace,
        resolved_space: str,
        namespace: str,
        workspace_ns: WorkspaceNamespaces,
        resolve_mode: str,
    ) -> GraphSpaceQueryResult | None:
        if graph_space == GraphSpace.BASE_KG:
            return self._result_for_base_kg_node(
                node=node,
                resolved_space=resolved_space,
                namespace=namespace,
                workspace_ns=workspace_ns,
                resolve_mode=resolve_mode,
            )
        if not self._node_matches_graph_space(node, graph_space):
            return None
        return GraphSpaceQueryResult(
            node=node,
            graph_space=resolved_space,
            namespace=namespace,
        )

    def _result_for_base_kg_node(
        self,
        *,
        node: Node,
        resolved_space: str,
        namespace: str,
        workspace_ns: WorkspaceNamespaces,
        resolve_mode: str,
    ) -> GraphSpaceQueryResult | None:
        logical_ref = logical_ref_from_entity(node)
        if logical_ref is None or logical_ref.target_namespace != workspace_ns.source_space:
            return None

        if resolve_mode == "pointer_only":
            return GraphSpaceQueryResult(
                node=node,
                graph_space=resolved_space,
                namespace=namespace,
                reference_node=node,
            )

        target = self._resolve_target_node(
            workspace_ns=workspace_ns,
            logical_ref=logical_ref,
            resolve_mode=resolve_mode,
        )
        if target is None:
            if resolve_mode == "include_dangling":
                return GraphSpaceQueryResult(
                    node=node,
                    graph_space=resolved_space,
                    namespace=namespace,
                    reference_node=node,
                )
            return None

        if resolve_mode == "target_only":
            return GraphSpaceQueryResult(
                node=target,
                graph_space=resolved_space,
                namespace=namespace,
            )

        return GraphSpaceQueryResult(
            node=target,
            graph_space=resolved_space,
            namespace=namespace,
            reference_node=node,
        )

    def _resolve_target_node(
        self,
        *,
        workspace_ns: WorkspaceNamespaces,
        logical_ref,
        resolve_mode: str,
    ) -> Node | None:
        if logical_ref.target_kind != "node":
            return None
        target_engine = self.engines.kg
        target_namespace = logical_ref.target_namespace
        target_resolve_mode = (
            "include_tombstones" if resolve_mode == "include_tombstone" else "redirect"
        )
        with _temporary_namespace(target_engine, target_namespace):
            nodes = list(
                target_engine.read.get_nodes(
                    ids=[logical_ref.target_id],
                    resolve_mode=target_resolve_mode,
                )
            )
        if not nodes:
            return None
        node = nodes[0]
        metadata = dict(getattr(node, "metadata", None) or {})
        if str(metadata.get("graph_space") or "").strip().lower() != GraphSpace.SOURCE.value:
            return None
        return node


__all__ = [
    "GraphSpaceQueryResult",
    "GraphSpaceQueryService",
    "workspace_graph_spaces",
]
