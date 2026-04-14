from __future__ import annotations

from dataclasses import dataclass


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
