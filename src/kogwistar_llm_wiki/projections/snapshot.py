"""Read-side projection snapshot construction and visibility filtering."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from kogwistar.engine_core.models import Edge, Node
from kogwistar_obsidian_sink.core.models import ProjectionEntity, SemanticRelationship

from ..configuration.workspace import GraphSpace, WorkspaceNamespaces
from ..models import NamespaceEngines, ProjectionSnapshot
from ..parsing.parse_views import ParseViewResolver
from ..policies.rules import LlmWikiPolicies
from ..utils import _temporary_namespace


class ProjectionSnapshotMixin:
    """Build the active, workspace-scoped view consumed by external sinks."""

    engines: NamespaceEngines
    policies: LlmWikiPolicies

    def build_projection_snapshot(
        self,
        workspace_id: str,
        *,
        graph_spaces: list[GraphSpace | str] | None = None,
        projection_filter: str | None = None,
    ) -> ProjectionSnapshot:
        """Returns the current graph-space visible state for a workspace."""
        ns = WorkspaceNamespaces(workspace_id)
        resolver = ParseViewResolver(
            self.engines.conversation.meta_sqlite,
            workspace_id=workspace_id,
        )
        requested_spaces = self._normalize_graph_spaces(graph_spaces)
        namespaces = [self._namespace_for_graph_space(ns, graph_space) for graph_space in requested_spaces]
        all_nodes = self._read_workspace_nodes(
            workspace_id=workspace_id,
            namespaces=namespaces,
        )
        all_nodes = [node for node in all_nodes if self._is_active_artifact(node, resolver)]
        # Edge reads are workspace-scoped first, with endpoint filtering kept as a
        # second line of defense for relationship visibility.
        all_edges = self._read_workspace_edges(
            workspace_id=workspace_id,
            namespaces=namespaces,
        )
        all_edges = [edge for edge in all_edges if self._is_active_artifact(edge, resolver)]
        manifest_ids = self._load_projection_manifest_ids(workspace_id) if requested_spaces == [GraphSpace.CURATED_KG] else None
        adjacency = self._build_adjacency(all_edges)

        visible_nodes = self._select_visible_nodes(
            all_nodes=all_nodes,
            requested_spaces=requested_spaces,
            manifest_ids=manifest_ids,
        )
        if str(projection_filter or "").strip().lower() == "demo":
            visible_nodes = self._apply_demo_projection_filter(
                visible_nodes=visible_nodes,
                adjacency=adjacency,
            )
        visible_ids = {str(node.id) for node in visible_nodes}

        source_ids_by_node: dict[str, set[str]] = defaultdict(set)
        target_ids_by_node: dict[str, set[str]] = defaultdict(set)
        relationships_by_source: dict[str, list[SemanticRelationship]] = defaultdict(list)
        seen_relationships: set[tuple[str, str, str, str]] = set()

        for edge in all_edges:
            edge_sources = [str(item) for item in (getattr(edge, "source_ids", None) or []) if str(item)]
            edge_targets = [str(item) for item in (getattr(edge, "target_ids", None) or []) if str(item)]
            if not edge_sources or not edge_targets:
                continue

            relation_type = str(getattr(edge, "relation", None) or getattr(edge, "label", None) or "related")
            properties: dict[str, object] = {}
            edge_metadata = dict(getattr(edge, "metadata", None) or {})
            edge_workspace_id = str(edge_metadata.get("workspace_id") or "").strip()
            if edge_workspace_id and edge_workspace_id != workspace_id:
                continue
            if edge_metadata:
                properties.update(edge_metadata)
            summary = getattr(edge, "summary", None)
            if summary:
                properties.setdefault("summary", str(summary))

            for source_id in edge_sources:
                if source_id not in visible_ids:
                    continue
                for target_id in edge_targets:
                    if target_id not in visible_ids:
                        continue
                    source_ids_by_node[source_id].add(target_id)
                    target_ids_by_node[target_id].add(source_id)
                    rel_key = (
                        source_id,
                        target_id,
                        relation_type,
                        json.dumps(properties, sort_keys=True, ensure_ascii=False),
                    )
                    if rel_key in seen_relationships:
                        continue
                    seen_relationships.add(rel_key)
                    relationships_by_source[source_id].append(
                        SemanticRelationship(
                            source_id=source_id,
                            target_id=target_id,
                            relation_type=relation_type,
                            properties=dict(properties),
                        )
                    )

        visible_nodes.sort(key=lambda node: (str(node.label), str(node.id)))

        return ProjectionSnapshot(
            entities=[
                ProjectionEntity(
                    kg_id=str(node.id),
                    title=str(node.label),
                    entity_type=str(node.type),
                    summary=str(node.summary),
                    metadata=dict(node.metadata or {}),
                    source_ids=sorted(set(list(getattr(node, "source_ids", []) or []) + list(source_ids_by_node.get(str(node.id), set())))),
                    target_ids=sorted(set(list(getattr(node, "target_ids", []) or []) + list(target_ids_by_node.get(str(node.id), set())))),
                    relation=getattr(node, "relation", None),
                    relationships=list(relationships_by_source.get(str(node.id), [])),
                    body=str(node.summary),
                )
                for node in visible_nodes
            ]
        )

    @staticmethod
    def _is_active_artifact(artifact: object, resolver: ParseViewResolver) -> bool:
        metadata = dict(getattr(artifact, "metadata", None) or {})
        source_id = str(metadata.get("source_document_id") or "").strip()
        if not source_id:
            return True
        return resolver.is_active_metadata(source_id, metadata)

    def _normalize_graph_spaces(self, graph_spaces: list[GraphSpace | str] | None) -> list[GraphSpace]:
        spaces = [GraphSpace.CURATED_KG] if graph_spaces is None else graph_spaces
        normalized: list[GraphSpace] = []
        for graph_space in spaces:
            if isinstance(graph_space, GraphSpace):
                normalized.append(graph_space)
            else:
                normalized.append(GraphSpace(str(graph_space).strip().lower()))
        if not normalized:
            raise ValueError("graph_spaces must not be empty")
        return normalized

    def _namespace_for_graph_space(self, ns: WorkspaceNamespaces, graph_space: GraphSpace) -> str:
        if graph_space == GraphSpace.SOURCE:
            return ns.source_space
        if graph_space == GraphSpace.BASE_KG:
            return ns.base_kg_space
        if graph_space == GraphSpace.CURATED_KG:
            return ns.curated_kg_space
        raise ValueError(f"Projection does not support graph space {graph_space.value!r}")

    def _node_graph_space(self, node: Node) -> str:
        metadata = dict(getattr(node, "metadata", None) or {})
        return str(metadata.get("graph_space") or "").strip().lower()

    def _select_visible_nodes(
        self,
        *,
        all_nodes: list[Node],
        requested_spaces: list[GraphSpace],
        manifest_ids: set[str] | None,
    ) -> list[Node]:
        selected: list[Node] = []
        seen_ids: set[str] = set()
        requested_space_values = {space.value for space in requested_spaces}
        for node in all_nodes:
            node_id = str(getattr(node, "id", "") or "")
            if not node_id or node_id in seen_ids:
                continue
            node_space = self._node_graph_space(node)
            if node_space not in requested_space_values:
                continue
            if manifest_ids is not None and node_id not in manifest_ids:
                continue
            if (
                manifest_ids is None
                and node_space == GraphSpace.CURATED_KG.value
                and not self.policies.projection.is_projection_eligible(dict(getattr(node, "metadata", None) or {}))
            ):
                continue
            selected.append(node)
            seen_ids.add(node_id)
        return selected

    def _apply_demo_projection_filter(self, *, visible_nodes: list[Node], adjacency: dict[str, set[str]]) -> list[Node]:
        filtered: list[Node] = []
        for node in visible_nodes:
            node_id = str(getattr(node, "id", "") or "")
            title = str(getattr(node, "label", "") or getattr(node, "summary", "") or "")
            if title.startswith("This is a starter document for the LLM-Wiki quickstart"):
                continue
            if self._is_sentence_like_title(title) and not adjacency.get(node_id):
                continue
            filtered.append(node)
        return filtered

    def _build_adjacency(self, edges: list[Edge]) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            edge_sources = [str(item) for item in (getattr(edge, "source_ids", None) or []) if str(item)]
            edge_targets = [str(item) for item in (getattr(edge, "target_ids", None) or []) if str(item)]
            for source_id in edge_sources:
                adjacency[source_id].update(edge_targets)
            for target_id in edge_targets:
                adjacency[target_id].update(edge_sources)
        return adjacency

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

    def _read_workspace_nodes(self, *, workspace_id: str, namespaces: list[str]) -> list[Node]:
        node_by_id: dict[str, Node] = {}
        for namespace in namespaces:
            for node in self._read_nodes(namespace=namespace, where={"workspace_id": workspace_id}):
                node_id = str(getattr(node, "id", "") or "")
                if not node_id or node_id in node_by_id:
                    continue
                node_by_id[node_id] = node
        return list(node_by_id.values())

    def _read_workspace_edges(self, *, workspace_id: str, namespaces: list[str]) -> list[Edge]:
        edge_by_id: dict[str, Edge] = {}
        for namespace in namespaces:
            for edge in self._read_edges(namespace=namespace, where={"workspace_id": workspace_id}):
                edge_id = str(getattr(edge, "id", "") or "")
                if not edge_id or edge_id in edge_by_id:
                    continue
                edge_by_id[edge_id] = edge
        return list(edge_by_id.values())

    def _read_nodes(self, *, namespace: str, where: dict[str, object]) -> list[Node]:
        with _temporary_namespace(self.engines.kg, namespace):
            return list(self.engines.kg.read.get_nodes(where=dict(where), limit=10_000))

    def _read_edges(self, *, namespace: str, where: dict[str, object]) -> list[Edge]:
        with _temporary_namespace(self.engines.kg, namespace):
            return list(self.engines.kg.read.get_edges(where=dict(where), limit=10_000))

    def _load_projection_manifest_ids(self, workspace_id: str) -> set[str] | None:
        meta = self.engines.conversation.meta_sqlite
        row = meta.get_named_projection(WorkspaceNamespaces(workspace_id).projection_manifest, workspace_id)
        if not row:
            return None
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001 - corrupt optional manifest is treated as absent
                return None
        if not isinstance(payload, dict):
            return None
        ready_ids = payload.get("ready_projected_ids", None)
        if isinstance(ready_ids, list):
            ids = ready_ids
        else:
            ids = payload.get("projected_ids")
        if not isinstance(ids, list):
            return None
        return {str(item) for item in ids if str(item)}


__all__ = ["ProjectionSnapshotMixin"]
