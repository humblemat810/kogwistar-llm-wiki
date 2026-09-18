"""Compatibility composition for graph snapshots and Obsidian sinks."""

from __future__ import annotations

from pathlib import Path

from kogwistar_obsidian_sink.integrations.kogwistar_adapter import KogwistarDuckProvider
from kogwistar_obsidian_sink.sinks.obsidian import ObsidianVaultSink

from .configuration.workspace import GraphSpace
from .models import NamespaceEngines, ObsidianBuildResult
from .policies.rules import LlmWikiPolicies, build_default_policies
from .projections.snapshot import ProjectionSnapshotMixin


class ProjectionManager(ProjectionSnapshotMixin):
    """Coordinate active graph snapshots with external vault sinks."""

    def __init__(self, engines: NamespaceEngines, *, policies: LlmWikiPolicies | None = None) -> None:
        self.engines = engines
        self.policies = policies or build_default_policies()

    def build_obsidian_vault(
        self,
        vault_root: str | Path,
        *,
        workspace_id: str,
        graph_spaces: list[GraphSpace | str] | None = None,
        projection_filter: str | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        """Fully materialize a new Obsidian vault from the current projection."""
        snapshot = self.build_projection_snapshot(
            workspace_id,
            graph_spaces=graph_spaces,
            projection_filter=projection_filter,
        )
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
        changed_ids: set[str] | None = None,
        deleted_ids: set[str] | None = None,
        affected_titles: set[str] | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        """Incrementally update an existing Obsidian vault."""
        snapshot = self.build_projection_snapshot(workspace_id)
        if changed_ids is None and deleted_ids is None and affected_titles is None:
            changed_ids = {entity.kg_id for entity in snapshot.entities}
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


__all__ = ["ProjectionManager"]
