from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Set

from kogwistar_obsidian_sink.core.models import ProjectionEntity
from kogwistar_obsidian_sink.integrations.kogwistar_adapter import KogwistarDuckProvider
from kogwistar_obsidian_sink.sinks.obsidian import ObsidianVaultSink

from .models import NamespaceEngines, ProjectionSnapshot, ObsidianBuildResult
from .namespaces import WorkspaceNamespaces


logger = logging.getLogger(__name__)


class ProjectionManager:
    """
    Coordinates the projection of the Knowledge Graph into external sinks (e.g., Obsidian).
    Acts as the composition layer between Kogwistar and the Obsidian Sink.
    """

    def __init__(self, engines: NamespaceEngines):
        self.engines = engines

    def build_projection_snapshot(self, workspace_id: str) -> ProjectionSnapshot:
        """Returns the current 'KG-visible' state for a workspace."""
        ns = WorkspaceNamespaces(workspace_id)
        # Authoritative filtering based on contract
        all_nodes = list(self.engines.kg.read.get_nodes(where={"workspace_id": workspace_id}))
        visible_nodes = [node for node in all_nodes if ns.is_kg_visible(node.metadata or {})]
        
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

    def build_obsidian_vault(
        self,
        vault_root: str | Path,
        *,
        workspace_id: str,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        """Fully materializes a new Obsidian vault from the current projection."""
        snapshot = self.build_projection_snapshot(workspace_id)
        provider = KogwistarDuckProvider(
            entities=snapshot.entities,
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
        workspace_id: str,
        changed_ids: Set[str] | None = None,
        deleted_ids: Set[str] | None = None,
        affected_titles: Set[str] | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        """Incrementally updates an existing Obsidian vault."""
        snapshot = self.build_projection_snapshot(workspace_id)
        provider = KogwistarDuckProvider(
            entities=snapshot.entities,
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
