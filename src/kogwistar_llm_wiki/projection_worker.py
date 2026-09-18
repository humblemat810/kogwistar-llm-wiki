"""Backward-compatible import for the durable projection worker."""

from .projections.worker_impl import ProjectionWorker

__all__ = ["ProjectionWorker"]
