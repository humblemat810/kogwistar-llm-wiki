"""Projection and Obsidian-vault access for the ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

from ..configuration.workspace import GraphSpace
from ..models import ObsidianBuildResult


class ProjectionAccessMixin:
    """Delegate projection operations through the pipeline's manager."""

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
        return self.projection.build_obsidian_vault(
            vault_root,
            workspace_id=workspace_id,
            graph_spaces=graph_spaces,
            projection_filter=projection_filter,
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
