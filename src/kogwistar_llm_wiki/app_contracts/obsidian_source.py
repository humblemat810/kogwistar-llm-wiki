"""Narrow host boundary for the optional Obsidian source plugin.

The source plugin owns vault parsing and rendering.  LLM-Wiki owns graph
authorization, revision checks, and mutation through its normal patch path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, Protocol

from kogwistar.json_types import JsonObject

ObsidianDeletionMode = Literal["ignore", "tombstone"]


class ObsidianSourceHost(Protocol):
    """Host operations exposed to an explicitly enabled source plugin."""

    def export_snapshot(self, workspace_id: str, vault_id: str, /) -> JsonObject: ...

    def scan_import(
        self,
        workspace_id: str,
        vault_id: str,
        payload: JsonObject,
        /,
    ) -> JsonObject: ...

    def plan_import(
        self,
        workspace_id: str,
        vault_id: str,
        payload: JsonObject,
        /,
    ) -> JsonObject: ...

    def apply_import(
        self,
        workspace_id: str,
        vault_id: str,
        plan_id: str,
        deletion_mode: ObsidianDeletionMode,
        /,
    ) -> JsonObject: ...

    def list_conflicts(self, workspace_id: str, vault_id: str, /) -> JsonObject: ...


class ObsidianSourceHostAdapter:
    """Typed adapter for the host callbacks supplied by LLM-Wiki.

    Keeping the callbacks explicit makes it impossible for a plugin instance
    to discover graph engines or storage paths by introspection.  The host
    remains responsible for ACL checks and revision-fenced patch application.
    """

    def __init__(
        self,
        *,
        export_snapshot: Callable[[str, str], JsonObject],
        scan_import: Callable[[str, str, JsonObject], JsonObject],
        plan_import: Callable[[str, str, JsonObject], JsonObject],
        apply_import: Callable[[str, str, str, ObsidianDeletionMode], JsonObject],
        list_conflicts: Callable[[str, str], JsonObject],
    ) -> None:
        callbacks = (export_snapshot, scan_import, plan_import, apply_import, list_conflicts)
        if any(not callable(callback) for callback in callbacks):
            raise TypeError("all Obsidian source host callbacks are required")
        self._export_snapshot = export_snapshot
        self._scan_import = scan_import
        self._plan_import = plan_import
        self._apply_import = apply_import
        self._list_conflicts = list_conflicts

    def export_snapshot(self, workspace_id: str, vault_id: str, /) -> JsonObject:
        return self._export_snapshot(workspace_id, vault_id)

    def scan_import(
        self, workspace_id: str, vault_id: str, payload: JsonObject, /
    ) -> JsonObject:
        return self._scan_import(workspace_id, vault_id, payload)

    def plan_import(
        self, workspace_id: str, vault_id: str, payload: JsonObject, /
    ) -> JsonObject:
        return self._plan_import(workspace_id, vault_id, payload)

    def apply_import(
        self,
        workspace_id: str,
        vault_id: str,
        plan_id: str,
        deletion_mode: ObsidianDeletionMode,
        /,
    ) -> JsonObject:
        return self._apply_import(workspace_id, vault_id, plan_id, deletion_mode)

    def list_conflicts(self, workspace_id: str, vault_id: str, /) -> JsonObject:
        return self._list_conflicts(workspace_id, vault_id)


def validate_source_request(payload: Mapping[str, object]) -> tuple[str, str]:
    """Validate the identity selectors shared by CLI and Workbench callers."""
    workspace_value = payload.get("workspace_id")
    vault_value = payload.get("vault_id")
    if not isinstance(workspace_value, str) or not isinstance(vault_value, str):
        raise ValueError("Obsidian source identity values must be strings")
    workspace_id = workspace_value.strip()
    vault_id = vault_value.strip()
    if not workspace_id or not vault_id:
        raise ValueError("Obsidian source requests require workspace_id and vault_id")
    if len(workspace_id) > 256 or len(vault_id) > 256:
        raise ValueError("Obsidian source identity values are too long")
    return workspace_id, vault_id


__all__ = [
    "ObsidianDeletionMode",
    "ObsidianSourceHost",
    "ObsidianSourceHostAdapter",
    "validate_source_request",
]
