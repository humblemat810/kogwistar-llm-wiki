from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkspaceNamespaces:
    workspace_id: str

    @property
    def conv_fg(self) -> str:
        return f"ws:{self.workspace_id}:conv:fg"

    @property
    def conv_bg(self) -> str:
        return f"ws:{self.workspace_id}:conv:bg"

    @property
    def workflow_maintenance(self) -> str:
        return f"ws:{self.workspace_id}:wf:maintenance"

    @property
    def review(self) -> str:
        return f"ws:{self.workspace_id}:review"

    @property
    def kg(self) -> str:
        return f"ws:{self.workspace_id}:kg"

    @property
    def wisdom(self) -> str:
        return f"ws:{self.workspace_id}:wisdom"

    @property
    def projection_jobs(self) -> str:
        return f"ws:{self.workspace_id}:projection_jobs"

    @property
    def maintenance_jobs(self) -> str:
        return f"ws:{self.workspace_id}:maintenance_jobs"

    @property
    def projection_state(self) -> str:
        return f"ws:{self.workspace_id}:projection_state"

    @property
    def projection_manifest(self) -> str:
        return f"ws:{self.workspace_id}:projection_manifest"

    def is_kg_visible(self, metadata: dict[str, Any]) -> bool:
        """Returns True if the artifact is visible to the Knowledge Graph / Projection."""
        return metadata.get("visibility") == "projection" or metadata.get("projection_visible") is True

    def get_lane_for_namespace(self, namespace: str) -> str | None:
        """Helper to map a namespace back to its conceptual lane."""
        if namespace == self.conv_fg:
            return "foreground"
        if namespace == self.conv_bg:
            return "background"
        if namespace == self.kg:
            return "knowledge"
        return None
