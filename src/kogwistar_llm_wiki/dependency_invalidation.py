"""Backward-compatible imports for maintenance dependency invalidation."""

from .maintenance.dependency_planning import (
    DependencyInvalidationPlan,
    plan_dependency_invalidation,
)

__all__ = ["DependencyInvalidationPlan", "plan_dependency_invalidation"]
