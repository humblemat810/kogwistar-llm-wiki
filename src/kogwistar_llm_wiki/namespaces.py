"""Backward-compatible facade for workspace namespace helpers."""

from .configuration.workspace import (
    GraphSpace,
    GraphSpaceNamespace,
    WorkspaceNamespaces,
    namespace_matches_graph_space_metadata,
)

__all__ = [
    "GraphSpace",
    "GraphSpaceNamespace",
    "WorkspaceNamespaces",
    "namespace_matches_graph_space_metadata",
]
