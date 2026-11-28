//! Service-health repair actions: classify a maintenance workspace as
//! healthy/degraded/unhealthy and select a bounded repair action.
//! Mirrors the service-ops repair surface from Python without coupling to a
//! particular backend.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ServiceHealth {
    Healthy,
    Degraded,
    Unhealthy,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RepairAction {
    None,
    RetryJob,
    RequeueSuspended,
    ResetStaleLease,
    EscalateToOperator,
}

pub const MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED: u64 = 3;
pub const MAX_CONSECUTIVE_FAILURES_BEFORE_UNHEALTHY: u64 = 7;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ServiceHealthReport {
    pub health: ServiceHealth,
    pub consecutive_failures: u64,
    pub repair_action: RepairAction,
    pub reason: String,
}

#[must_use]
pub fn classify_health(consecutive_failures: u64, last_error_escalated: bool) -> ServiceHealth {
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES_BEFORE_UNHEALTHY || last_error_escalated {
        ServiceHealth::Unhealthy
    } else if consecutive_failures >= MAX_CONSECUTIVE_FAILURES_BEFORE_DEGRADED {
        ServiceHealth::Degraded
    } else {
        ServiceHealth::Healthy
    }
}

#[must_use]
pub fn repair_action_for(health: ServiceHealth) -> RepairAction {
    match health {
        ServiceHealth::Healthy => RepairAction::None,
        ServiceHealth::Degraded => RepairAction::RetryJob,
        ServiceHealth::Unhealthy => RepairAction::EscalateToOperator,
    }
}

#[must_use]
pub fn build_health_report(
    consecutive_failures: u64,
    last_error_escalated: bool,
) -> ServiceHealthReport {
    let health = classify_health(consecutive_failures, last_error_escalated);
    let repair_action = repair_action_for(health);
    let reason = match health {
        ServiceHealth::Healthy => "no consecutive failures; service healthy".to_owned(),
        ServiceHealth::Degraded => {
            format!(
                "consecutive failures {} exceed degraded threshold",
                consecutive_failures
            )
        }
        ServiceHealth::Unhealthy => {
            if last_error_escalated {
                "last error required escalation; service unhealthy".to_owned()
            } else {
                format!(
                    "consecutive failures {} exceed unhealthy threshold",
                    consecutive_failures
                )
            }
        }
    };
    ServiceHealthReport {
        health,
        consecutive_failures,
        repair_action,
        reason,
    }
}
