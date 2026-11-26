use kogwistar_llm_wiki_port::maintenance_strategies::{
    maintenance_strategy_kind, MaintenanceStrategyKind,
};

#[test]
fn default_maintenance_strategy_registry_selects_specific_strategies_first() {
    let cases = [
        ("execution_wisdom", MaintenanceStrategyKind::ExecutionWisdom),
        (
            "distill_to_wisdom",
            MaintenanceStrategyKind::ExecutionWisdom,
        ),
        (
            "graph_patch_apply",
            MaintenanceStrategyKind::GraphPatchApply,
        ),
        (
            "document_propose_crosslinks",
            MaintenanceStrategyKind::CrosslinkLifecycle,
        ),
        (
            "document_validate_crosslinks",
            MaintenanceStrategyKind::CrosslinkLifecycle,
        ),
        (
            "document_revalidate_crosslinks",
            MaintenanceStrategyKind::CrosslinkLifecycle,
        ),
        (
            "document_retract_crosslinks",
            MaintenanceStrategyKind::CrosslinkLifecycle,
        ),
        (
            "review_maintenance_subject",
            MaintenanceStrategyKind::MaintenanceObservation,
        ),
        (
            "entity_disambiguation_scan",
            MaintenanceStrategyKind::ContactDisambiguationScan,
        ),
        (
            "document_parse_graph",
            MaintenanceStrategyKind::DocumentParse,
        ),
        (
            "document_expand_parse_children",
            MaintenanceStrategyKind::DocumentExpandParseChildren,
        ),
        (
            "document_reparse_region",
            MaintenanceStrategyKind::DocumentExpandParseChildren,
        ),
        (
            "document_correct_parse_children",
            MaintenanceStrategyKind::GraphPatchProposal,
        ),
        ("distill", MaintenanceStrategyKind::RuntimeWorkflow),
    ];
    for (maintenance_kind, expected) in cases {
        assert_eq!(maintenance_strategy_kind(Some(maintenance_kind)), expected);
    }
}
