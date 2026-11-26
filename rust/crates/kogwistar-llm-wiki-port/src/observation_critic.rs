use serde::Serialize;
use serde_json::Value;

pub const CONTEXT_LIMIT_MARKERS: [&str; 13] = [
    "context_length_exceeded",
    "maximum context length",
    "maximum prompt tokens",
    "context window exceeded",
    "context window is too small",
    "context window",
    "prompt is too long",
    "prompt exceeds the available context",
    "exceeds the available context size",
    "input length exceeds",
    "input token count exceeds",
    "input is too long",
    "too many tokens",
];

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ObservationCriticOutput {
    pub findings: Vec<Value>,
}

fn marker_in_details(details: &str) -> bool {
    let details = details.to_ascii_lowercase();
    CONTEXT_LIMIT_MARKERS
        .iter()
        .any(|marker| details.contains(marker))
}

#[must_use]
pub fn is_context_window_error(
    message: Option<&str>,
    code: Option<&str>,
    body: Option<&str>,
    exception_name: Option<&str>,
) -> bool {
    let details = format!(
        "{} {} {}",
        message.unwrap_or_default(),
        code.unwrap_or_default(),
        body.unwrap_or_default()
    )
    .to_ascii_lowercase();
    exception_name
        .unwrap_or_default()
        .to_ascii_lowercase()
        .contains("contextoverflow")
        || marker_in_details(&details)
}

pub fn validate_observation_critic_output(
    output: &ObservationCriticOutput,
    subject_id: &str,
    evidence_ids: &[String],
) -> Result<(), String> {
    if output.findings.len() > 4 {
        return Err("observation critic findings must not exceed four entries".to_owned());
    }
    for finding in &output.findings {
        let finding_message = finding
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if finding_message.chars().count() > 200 {
            return Err(
                "observation critic finding messages must not exceed 200 characters".to_owned(),
            );
        }
        if finding
            .get("subject_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            != subject_id
        {
            return Err("critic finding subject_id does not match the reviewed subject".to_owned());
        }
        let cited = finding
            .get("evidence_ids")
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        if cited.is_empty()
            || cited
                .iter()
                .any(|evidence_id| !evidence_ids.contains(evidence_id))
        {
            return Err(
                "critic finding must cite evidence IDs present in the observation frame".to_owned(),
            );
        }
    }
    Ok(())
}

#[must_use]
pub fn safe_observation_critic_error_code(
    structured_schema_validation: bool,
    parse_failure: bool,
    output_limit_reached: bool,
    value_error_message: Option<&str>,
) -> &'static str {
    if structured_schema_validation {
        return "structured_schema_validation";
    }
    if parse_failure {
        return "structured_output_parse_failure";
    }
    if output_limit_reached {
        return "output_token_limit";
    }
    match value_error_message {
        Some("observation critic finding messages must not exceed 200 characters") => {
            "finding_message_too_long"
        }
        Some("critic finding subject_id does not match the reviewed subject") => {
            "finding_subject_mismatch"
        }
        Some("critic finding must cite evidence IDs present in the observation frame") => {
            "finding_evidence_unmatched"
        }
        Some(_) => "invalid_critic_output",
        None => "provider_failure",
    }
}
