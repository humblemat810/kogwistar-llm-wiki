use std::fmt;

use serde::Serialize;
use serde_json::{json, Value};

use crate::ids::stable_id;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MaintenanceClaim {
    pub job_id: String,
    pub claim_token: Option<String>,
    pub claim_lost: bool,
}

impl MaintenanceClaim {
    #[must_use]
    pub fn new(job_id: &str, claim_token: Option<&str>) -> Self {
        Self {
            job_id: job_id.to_owned(),
            claim_token: claim_token.map(str::to_owned),
            claim_lost: false,
        }
    }

    #[must_use]
    pub fn owned(&self) -> bool {
        !self.claim_lost
    }

    pub fn assert_owned(
        &mut self,
        reason: &str,
        now_ms: u64,
        worker_id: &str,
        thread_name: &str,
    ) -> Result<(), ClaimLostError> {
        if self.owned() {
            return Ok(());
        }
        Err(ClaimLostError {
            event: stale_claim_event(self, reason, now_ms, worker_id, thread_name),
            reason: reason.to_owned(),
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LeaseQueueError {
    Provider(String),
}

impl fmt::Display for LeaseQueueError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Provider(message) => write!(formatter, "{message}"),
        }
    }
}

impl std::error::Error for LeaseQueueError {}

pub trait LeaseQueue {
    fn renew_lease(&mut self, lease_seconds: u64) -> Result<bool, LeaseQueueError>;
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub enum LeaseRenewalAction {
    Continue,
    FenceClaim,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct LeaseRenewalDecision {
    pub action: LeaseRenewalAction,
    pub claim_lost: bool,
    pub event: Value,
}

pub fn lease_renewal_decision(
    claim: &mut MaintenanceClaim,
    renewal_result: Result<bool, LeaseQueueError>,
    lease_seconds: u64,
    idle_seconds: u64,
    now_ms: u64,
) -> LeaseRenewalDecision {
    match renewal_result {
        Ok(true) => LeaseRenewalDecision {
            action: LeaseRenewalAction::Continue,
            claim_lost: false,
            event: json!({
                "event": "maintenance_lease_renewed",
                "job_id": claim.job_id,
                "lease_seconds": lease_seconds,
                "idle_seconds": round_two(idle_seconds),
                "timestamp_ms": now_ms
            }),
        },
        Ok(false) => {
            claim.claim_lost = true;
            LeaseRenewalDecision {
                action: LeaseRenewalAction::FenceClaim,
                claim_lost: true,
                event: json!({
                    "event": "maintenance_stale_claim_rejected",
                    "job_id": claim.job_id,
                    "reason": "lease_ownership_lost",
                    "timestamp_ms": now_ms
                }),
            }
        }
        Err(error) => {
            claim.claim_lost = true;
            let message = error.to_string();
            LeaseRenewalDecision {
                action: LeaseRenewalAction::FenceClaim,
                claim_lost: true,
                event: json!({
                    "event": "maintenance_lease_renewal_failed",
                    "job_id": claim.job_id,
                    "error_type": "LeaseQueueError",
                    "error": message,
                    "timestamp_ms": now_ms
                }),
            }
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ClaimLostError {
    pub reason: String,
    pub event: Value,
}

impl fmt::Display for ClaimLostError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "maintenance claim is no longer owned")
    }
}

impl std::error::Error for ClaimLostError {}

#[must_use]
pub fn stale_claim_event(
    claim: &MaintenanceClaim,
    reason: &str,
    now_ms: u64,
    worker_id: &str,
    thread_name: &str,
) -> Value {
    json!({
        "event": "maintenance_repeated_work_discarded",
        "job_id": claim.job_id,
        "reason": reason,
        "authoritative_result": false,
        "worker_id": worker_id,
        "thread_name": thread_name,
        "timestamp_ms": now_ms
    })
}

#[must_use]
pub fn maintenance_reply_idempotency_key(
    workspace_id: &str,
    reply_to_message_id: &str,
    status: &str,
) -> String {
    let msg_type = format!("reply.maintenance.{status}");
    let correlation_id = reply_to_message_id;
    stable_id(
        "kogwistar_llm_wiki.maintenance_reply",
        &[
            workspace_id,
            reply_to_message_id,
            msg_type.as_str(),
            correlation_id,
        ],
    )
    .to_string()
}

fn round_two(value: u64) -> f64 {
    (value as f64 * 100.0).round() / 100.0
}
