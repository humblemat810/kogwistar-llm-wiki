"""Durable projection workers and projection orchestration."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import ProjectionSnapshotMixin
    from .worker_impl import ProjectionWorker

__all__ = ["ProjectionSnapshotMixin", "ProjectionWorker"]


def __getattr__(name: str) -> object:
    if name == "ProjectionSnapshotMixin":
        from .snapshot import ProjectionSnapshotMixin

        return ProjectionSnapshotMixin
    if name == "ProjectionWorker":
        from .worker_impl import ProjectionWorker

        return ProjectionWorker
    raise AttributeError(name)
