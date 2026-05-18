"""App-level graph-space namespace helpers for `kogwistar-llm-wiki`.

This module keeps the semantic routing vocabulary explicit for the app layer:
workspace scope, graph spaces, lane namespaces, and namespace/metadata
agreement checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class GraphSpace(str, Enum):
    SOURCE = "source"
    BASE_KG = "base_kg"
    CURATED_KG = "curated_kg"
    CONVERSATION = "conversation"
    WORKFLOW = "workflow"
    REVIEW = "review"
    WISDOM = "wisdom"
    POLICY = "policy"
    PROJECTION = "projection"


@dataclass(frozen=True, slots=True)
class GraphSpaceNamespace:
    workspace_id: str
    graph_space: GraphSpace
    lane: str | None = None

    def as_string(self) -> str:
        base = f"ws:{self.workspace_id}:g:{self.graph_space.value}"
        if self.lane:
            return f"{base}:lane:{self.lane}"
        return base

    def metadata(self, *, legacy_namespace: str | None = None) -> dict[str, str]:
        data: dict[str, str] = {
            "workspace_id": self.workspace_id,
            "graph_space": self.graph_space.value,
        }
        if self.lane:
            data["graph_lane"] = self.lane
        if legacy_namespace is not None:
            data["legacy_namespace"] = legacy_namespace
        return data


@dataclass(frozen=True, slots=True)
class WorkspaceNamespaces:
    workspace_id: str

    def space(self, graph_space: GraphSpace, *, lane: str | None = None) -> str:
        return self.space_descriptor(graph_space, lane=lane).as_string()

    def space_descriptor(self, graph_space: GraphSpace, *, lane: str | None = None) -> GraphSpaceNamespace:
        return GraphSpaceNamespace(workspace_id=self.workspace_id, graph_space=graph_space, lane=lane)

    @property
    def conv_fg(self) -> str:
        return f"ws:{self.workspace_id}:conv:fg"

    @property
    def conv_bg(self) -> str:
        return f"ws:{self.workspace_id}:conv:bg"

    @property
    def conversation_fg_space(self) -> str:
        return self.space(GraphSpace.CONVERSATION, lane="foreground")

    @property
    def conversation_bg_space(self) -> str:
        return self.space(GraphSpace.CONVERSATION, lane="background")

    @property
    def workflow_maintenance(self) -> str:
        return f"ws:{self.workspace_id}:wf:maintenance"

    @property
    def workflow_space(self) -> str:
        return self.space(GraphSpace.WORKFLOW)

    @property
    def review(self) -> str:
        return f"ws:{self.workspace_id}:review"

    @property
    def review_space(self) -> str:
        return self.space(GraphSpace.REVIEW)

    @property
    def curated_kg_space(self) -> str:
        return self.space(GraphSpace.CURATED_KG)

    @property
    def source_space(self) -> str:
        return self.space(GraphSpace.SOURCE)

    @property
    def base_kg_space(self) -> str:
        return self.space(GraphSpace.BASE_KG)

    @property
    def derived_knowledge(self) -> str:
        return f"ws:{self.workspace_id}:derived_knowledge"

    @property
    def wisdom(self) -> str:
        return f"ws:{self.workspace_id}:wisdom"

    @property
    def wisdom_space(self) -> str:
        return self.space(GraphSpace.WISDOM)

    @property
    def policy_space(self) -> str:
        return self.space(GraphSpace.POLICY)

    @property
    def projection_space(self) -> str:
        return self.space(GraphSpace.PROJECTION)

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


def namespace_matches_graph_space_metadata(namespace: str, metadata: Mapping[str, Any]) -> bool:
    parts = str(namespace or "").split(":")
    if len(parts) < 4 or parts[0] != "ws" or parts[2] != "g":
        return False

    workspace_id = parts[1]
    graph_space = parts[3]
    lane = parts[5] if len(parts) >= 6 and parts[4] == "lane" else None

    meta = dict(metadata or {})
    if str(meta.get("workspace_id") or "") != workspace_id:
        return False
    if str(meta.get("graph_space") or "") != graph_space:
        return False
    if lane is not None and str(meta.get("graph_lane") or "") != lane:
        return False
    return True
