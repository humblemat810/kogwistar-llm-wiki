from __future__ import annotations

from kogwistar_llm_wiki.maintenance.maintenance_strategies import (
    CrosslinkMaintenanceStrategy,
    DocumentExpandParseChildrenMaintenanceStrategy,
    ExecutionWisdomMaintenanceStrategy,
    GraphPatchApplyMaintenanceStrategy,
    RuntimeWorkflowMaintenanceStrategy,
    build_default_maintenance_strategy_registry,
)


def test_default_maintenance_strategy_registry_selects_specific_strategies_first() -> None:
    registry = build_default_maintenance_strategy_registry()

    assert isinstance(registry.resolve("execution_wisdom"), ExecutionWisdomMaintenanceStrategy)
    assert isinstance(registry.resolve("distill_to_wisdom"), ExecutionWisdomMaintenanceStrategy)
    assert isinstance(registry.resolve("graph_patch_apply"), GraphPatchApplyMaintenanceStrategy)
    assert isinstance(registry.resolve("document_propose_crosslinks"), CrosslinkMaintenanceStrategy)
    assert isinstance(registry.resolve("document_validate_crosslinks"), CrosslinkMaintenanceStrategy)
    assert isinstance(registry.resolve("document_revalidate_crosslinks"), CrosslinkMaintenanceStrategy)
    assert isinstance(registry.resolve("document_retract_crosslinks"), CrosslinkMaintenanceStrategy)
    assert isinstance(
        registry.resolve("document_expand_parse_children"),
        DocumentExpandParseChildrenMaintenanceStrategy,
    )
    assert isinstance(
        registry.resolve("document_reparse_region"),
        DocumentExpandParseChildrenMaintenanceStrategy,
    )
    assert isinstance(registry.resolve("distill"), RuntimeWorkflowMaintenanceStrategy)
