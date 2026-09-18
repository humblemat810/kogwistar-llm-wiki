"""Public maintenance-domain facade.

Implementation modules retain their historical flat names for compatibility;
new application code can discover the maintenance boundary through this
package without importing the worker or daemon.
"""

from .maintenance_policy import (
    is_execution_wisdom_kind,
    is_graph_patch_kind,
    normalize_maintenance_kind,
    workflow_id_for_maintenance_kind,
)
from .maintenance_profiles import (
    MaintenanceProfileDecision,
    MaintenanceProfileLadderDecision,
    MaintenanceProfileLevel,
    add_usage,
    budget_fits,
    configured_maintenance_budget,
    configured_maintenance_enabled,
    configured_maintenance_profile,
    configured_prices,
    configured_profile_ladder,
    configured_token_budget_rate,
    next_window_reset,
    normalize_budget,
    normalize_profile_ladder,
    reset_expired_spend,
    resolve_profile,
    resolve_profile_ladder,
)
from .maintenance_selection import (
    MaintenanceCandidate,
    select_embedding_exploration,
    select_request_candidates,
)
from .maintenance_statistics import (
    build_maintenance_statistics,
    operation_category,
)
from .maintenance_strategies import (
    MaintenanceJobExecutionContext,
    MaintenanceStrategy,
    MaintenanceStrategyRegistry,
    build_default_maintenance_strategy_registry,
)

__all__ = [
    "MaintenanceCandidate",
    "MaintenanceJobExecutionContext",
    "MaintenanceProfileDecision",
    "MaintenanceProfileLadderDecision",
    "MaintenanceProfileLevel",
    "MaintenanceStrategy",
    "MaintenanceStrategyRegistry",
    "add_usage",
    "budget_fits",
    "build_default_maintenance_strategy_registry",
    "build_maintenance_statistics",
    "configured_maintenance_budget",
    "configured_maintenance_enabled",
    "configured_maintenance_profile",
    "configured_prices",
    "configured_profile_ladder",
    "configured_token_budget_rate",
    "is_execution_wisdom_kind",
    "is_graph_patch_kind",
    "next_window_reset",
    "normalize_budget",
    "normalize_maintenance_kind",
    "normalize_profile_ladder",
    "operation_category",
    "reset_expired_spend",
    "resolve_profile",
    "resolve_profile_ladder",
    "select_embedding_exploration",
    "select_request_candidates",
    "workflow_id_for_maintenance_kind",
]
