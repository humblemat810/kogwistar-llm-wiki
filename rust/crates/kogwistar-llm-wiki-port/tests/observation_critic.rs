use kogwistar_llm_wiki_port::observation_critic::{
    is_context_window_error, safe_observation_critic_error_code,
    validate_observation_critic_output, ObservationCriticOutput,
};
use serde_json::{json, Value};

fn finding() -> Value {
    json!({
        "code": "weak_label",
        "verdict": "weak_label",
        "severity": "warning",
        "subject_id": "node-1",
        "evidence_ids": ["node-1"],
        "message": "The label is underspecified."
    })
}

#[test]
fn critic_output_is_bounded_and_evidence_bound() {
    let output = ObservationCriticOutput {
        findings: vec![finding()],
    };
    assert!(validate_observation_critic_output(&output, "node-1", &["node-1".to_owned()]).is_ok());

    let mut long_finding = finding();
    long_finding["message"] = json!("x".repeat(201));
    let long = ObservationCriticOutput {
        findings: vec![long_finding],
    };
    assert!(
        validate_observation_critic_output(&long, "node-1", &["node-1".to_owned()])
            .unwrap_err()
            .contains("200 characters")
    );

    let mut wrong_subject_finding = finding();
    wrong_subject_finding["subject_id"] = json!("different-node");
    let wrong_subject = ObservationCriticOutput {
        findings: vec![wrong_subject_finding],
    };
    assert!(
        validate_observation_critic_output(&wrong_subject, "node-1", &["node-1".to_owned()])
            .unwrap_err()
            .contains("subject_id")
    );
}

#[test]
fn critic_error_codes_match_python_contract() {
    assert_eq!(
        safe_observation_critic_error_code(true, false, false, None),
        "structured_schema_validation"
    );
    assert_eq!(
        safe_observation_critic_error_code(false, true, false, None),
        "structured_output_parse_failure"
    );
    assert_eq!(
        safe_observation_critic_error_code(false, false, true, None),
        "output_token_limit"
    );
    assert_eq!(
        safe_observation_critic_error_code(
            false,
            false,
            false,
            Some("observation critic finding messages must not exceed 200 characters")
        ),
        "finding_message_too_long"
    );
    assert_eq!(
        safe_observation_critic_error_code(false, false, false, Some("invalid")),
        "invalid_critic_output"
    );
    assert_eq!(
        safe_observation_critic_error_code(false, false, false, None),
        "provider_failure"
    );
}

#[test]
fn context_window_error_detection_matches_common_markers() {
    assert!(is_context_window_error(
        Some("This model's maximum context length is 8192 tokens"),
        None,
        None,
        Some("BadRequestError")
    ));
    assert!(is_context_window_error(
        None,
        None,
        None,
        Some("ContextOverflowError")
    ));
    assert!(!is_context_window_error(
        Some("provider timed out"),
        None,
        None,
        Some("TimeoutError")
    ));
}
