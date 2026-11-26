use kogwistar_llm_wiki_port::maintenance_lease::{
    lease_renewal_decision, maintenance_reply_idempotency_key, LeaseQueue, LeaseQueueError,
    LeaseRenewalAction, MaintenanceClaim,
};

#[derive(Debug, Default)]
struct SuccessQueue;

impl LeaseQueue for SuccessQueue {
    fn renew_lease(&mut self, _lease_seconds: u64) -> Result<bool, LeaseQueueError> {
        Ok(true)
    }
}

#[derive(Debug, Default)]
struct LostQueue;

impl LeaseQueue for LostQueue {
    fn renew_lease(&mut self, _lease_seconds: u64) -> Result<bool, LeaseQueueError> {
        Ok(false)
    }
}

#[derive(Debug, Default)]
struct FailedQueue;

impl LeaseQueue for FailedQueue {
    fn renew_lease(&mut self, _lease_seconds: u64) -> Result<bool, LeaseQueueError> {
        Err(LeaseQueueError::Provider(
            "durable store unavailable".to_owned(),
        ))
    }
}

#[test]
fn successful_renewal_continues_ownership() {
    let mut claim = MaintenanceClaim::new("job-1", Some("claim-1"));
    let mut queue = SuccessQueue;
    let decision = lease_renewal_decision(&mut claim, queue.renew_lease(150), 150, 10, 123);

    assert_eq!(decision.action, LeaseRenewalAction::Continue);
    assert!(!decision.claim_lost);
    assert!(claim.owned());
    assert_eq!(
        decision.event,
        serde_json::json!({
            "event": "maintenance_lease_renewed",
            "job_id": "job-1",
            "lease_seconds": 150,
            "idle_seconds": 10.0,
            "timestamp_ms": 123
        })
    );
}

#[test]
fn lost_renewal_fences_claim() {
    let mut claim = MaintenanceClaim::new("job-1", Some("claim-1"));
    let mut queue = LostQueue;
    let decision = lease_renewal_decision(&mut claim, queue.renew_lease(150), 150, 10, 123);

    assert_eq!(decision.action, LeaseRenewalAction::FenceClaim);
    assert!(decision.claim_lost);
    assert!(!claim.owned());
    assert_eq!(
        decision.event,
        serde_json::json!({
            "event": "maintenance_stale_claim_rejected",
            "job_id": "job-1",
            "reason": "lease_ownership_lost",
            "timestamp_ms": 123
        })
    );

    let error = claim
        .assert_owned("post_graph_patch", 124, "worker-1", "thread-1")
        .unwrap_err();
    assert_eq!(error.reason, "post_graph_patch");
    assert_eq!(
        error.event,
        serde_json::json!({
            "event": "maintenance_repeated_work_discarded",
            "job_id": "job-1",
            "reason": "post_graph_patch",
            "authoritative_result": false,
            "worker_id": "worker-1",
            "thread_name": "thread-1",
            "timestamp_ms": 124
        })
    );
}

#[test]
fn failed_renewal_fences_claim() {
    let mut claim = MaintenanceClaim::new("job-renewal", Some("claim-1"));
    let mut queue = FailedQueue;
    let decision = lease_renewal_decision(&mut claim, queue.renew_lease(150), 150, 0, 123);

    assert_eq!(decision.action, LeaseRenewalAction::FenceClaim);
    assert!(decision.claim_lost);
    assert_eq!(
        decision.event,
        serde_json::json!({
            "event": "maintenance_lease_renewal_failed",
            "job_id": "job-renewal",
            "error_type": "LeaseQueueError",
            "error": "durable store unavailable",
            "timestamp_ms": 123
        })
    );
}

#[test]
fn maintenance_reply_key_matches_python_stable_id_contract() {
    assert_eq!(
        maintenance_reply_idempotency_key("workspace-1", "reply-message-1", "completed"),
        "6335b305-c4b5-5fa0-9e61-4ce34d78f802"
    );
}
