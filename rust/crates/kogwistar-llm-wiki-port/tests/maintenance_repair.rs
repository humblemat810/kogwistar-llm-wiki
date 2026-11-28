use kogwistar_llm_wiki_port::maintenance_repair::{
    build_health_report, classify_health, repair_action_for, RepairAction, ServiceHealth,
    MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED, MAX_CONSECUTIVE_FAILURES_BEFORE_UNHEALTHY,
};

#[test]
fn classify_health_below_threshold_is_healthy() {
    assert_eq!(classify_health(0, false), ServiceHealth::Healthy);
    assert_eq!(
        classify_health(MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED - 1, false),
        ServiceHealth::Healthy
    );
}

#[test]
fn classify_health_at_degraded_threshold() {
    assert_eq!(
        classify_health(MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED, false),
        ServiceHealth::Degraded
    );
}

#[test]
fn classify_health_at_unhealthy_threshold() {
    assert_eq!(
        classify_health(MAX_CONSECUTIVE_FAILURES_BEFORE_UNHEALTHY, false),
        ServiceHealth::Unhealthy
    );
}

#[test]
fn classify_health_escalated_is_unhealthy_regardless_of_failures() {
    assert_eq!(classify_health(0, true), ServiceHealth::Unhealthy);
    assert_eq!(
        classify_health(MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED - 1, true),
        ServiceHealth::Unhealthy
    );
}

#[test]
fn repair_action_matches_health() {
    assert_eq!(
        repair_action_for(ServiceHealth::Healthy),
        RepairAction::None
    );
    assert_eq!(
        repair_action_for(ServiceHealth::Degraded),
        RepairAction::RetryJob
    );
    assert_eq!(
        repair_action_for(ServiceHealth::Unhealthy),
        RepairAction::EscalateToOperator
    );
}

#[test]
fn build_health_report_carries_reason_and_action() {
    let healthy = build_health_report(0, false);
    assert_eq!(healthy.health, ServiceHealth::Healthy);
    assert_eq!(healthy.repair_action, RepairAction::None);
    assert!(healthy.reason.contains("healthy"));

    let degraded = build_health_report(MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED, false);
    assert_eq!(degraded.health, ServiceHealth::Degraded);
    assert_eq!(degraded.repair_action, RepairAction::RetryJob);

    let unhealthy = build_health_report(MAX_CONSECUTIVE_FAILURES_BEFORE_UNHEALTHY, false);
    assert_eq!(unhealthy.health, ServiceHealth::Unhealthy);
    assert_eq!(unhealthy.repair_action, RepairAction::EscalateToOperator);
}
