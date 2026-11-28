use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::ids::stable_id;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservationSubject {
    pub kind: String,
    pub subject_id: String,
    pub workspace_id: String,
    pub namespace: String,
    pub source_document_id: Option<String>,
    pub revision_id: Option<String>,
    pub revision_document_id: Option<String>,
    pub source_digest: Option<String>,
    pub parse_member_id: Option<String>,
    pub acl_scope: Option<String>,
    pub embedding_profile_fingerprint: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservationFinding {
    pub code: String,
    pub verdict: String,
    pub severity: String,
    pub subject_id: String,
    #[serde(default)]
    pub evidence_ids: Vec<String>,
    pub message: String,
    pub recommended_action: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MaintenanceObservationFrame {
    pub frame_id: String,
    pub subject: ObservationSubject,
    pub source_context: Vec<Value>,
    pub relation_context: Vec<Value>,
    pub neighborhood_context: Vec<Value>,
    pub parent_context: Vec<Value>,
    pub omitted_counts: BTreeMap<String, i64>,
    pub token_budget: i64,
    pub source_evidence_required: bool,
    pub active_view_id: Option<String>,
    pub active_view_version: Option<i64>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ParseAndGraphQualityAssessment {
    pub assessment_id: String,
    pub watermark_key: String,
    pub frame_id: String,
    pub subject: ObservationSubject,
    pub verdict: String,
    pub recommended_action: String,
    pub findings: Vec<Value>,
    pub active_view_id: Option<String>,
    pub active_view_version: Option<i64>,
    pub evaluator_version: String,
    pub critic_status: String,
    pub continuation_allowed: bool,
    pub watermark_expiry_seconds: i64,
}

fn bounded_records(records: &[Value], token_budget: i64) -> (Vec<Value>, i64) {
    let mut output = Vec::new();
    let mut used = 0_i64;
    let mut omitted = 0_i64;
    for record in records {
        let size = ((serde_json::to_string(record)
            .expect("observation record")
            .len() as i64)
            / 4)
        .max(1);
        if used + size > token_budget {
            omitted += 1;
            continue;
        }
        output.push(record.clone());
        used += size;
    }
    (output, omitted)
}

fn scoped_records(records: Vec<Value>, subject: &ObservationSubject) -> Vec<Value> {
    let source_namespace = format!("ws:{}:g:source", subject.workspace_id);
    records
        .into_iter()
        .filter(|record| {
            let workspace_id = record
                .get("workspace_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim();
            if !workspace_id.is_empty() && workspace_id != subject.workspace_id {
                return false;
            }
            let namespace = record
                .get("namespace")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim();
            if !namespace.is_empty()
                && namespace != subject.namespace
                && !(record.get("evidence_role") == Some(&Value::from("authoritative_source"))
                    && namespace == source_namespace)
            {
                return false;
            }
            if record.get("acl_authorized") == Some(&Value::from(false))
                || record.get("authorized") == Some(&Value::from(false))
                || record.get("profile_compatible") == Some(&Value::from(false))
            {
                return false;
            }
            let record_revision = record
                .get("source_revision_id")
                .or_else(|| record.get("revision_id"))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim();
            if subject
                .revision_id
                .as_deref()
                .is_some_and(|expected| !record_revision.is_empty() && record_revision != expected)
            {
                return false;
            }
            let record_revision_document = record
                .get("revision_document_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim();
            if subject
                .revision_document_id
                .as_deref()
                .is_some_and(|expected| {
                    !record_revision_document.is_empty() && record_revision_document != expected
                })
            {
                return false;
            }
            let record_digest = record
                .get("source_digest")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim()
                .to_ascii_lowercase();
            if subject.source_digest.as_deref().is_some_and(|expected| {
                !record_digest.is_empty() && record_digest != expected.to_ascii_lowercase()
            }) {
                return false;
            }
            let record_profile = record
                .get("embedding_profile_fingerprint")
                .or_else(|| record.get("profile_fingerprint"))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim();
            if subject
                .embedding_profile_fingerprint
                .as_deref()
                .is_some_and(|expected| !record_profile.is_empty() && record_profile != expected)
            {
                return false;
            }
            true
        })
        .collect()
}

fn record_json(value: &Value) -> String {
    serde_json::to_string(value).expect("observation evidence")
}

pub struct ObservationFrameInputs<'a> {
    pub subject: &'a ObservationSubject,
    pub source_context: &'a [Value],
    pub relation_context: &'a [Value],
    pub neighborhood_context: &'a [Value],
    pub parent_context: &'a [Value],
    pub token_budget: i64,
    pub active_view_id: Option<&'a str>,
    pub active_view_version: Option<i64>,
    pub source_evidence_required: bool,
}

#[must_use]
pub fn build_observation_frame(inputs: &ObservationFrameInputs<'_>) -> MaintenanceObservationFrame {
    let budget = inputs.token_budget.max(256);
    let (source, omitted_source) = bounded_records(
        &scoped_records(inputs.source_context.to_vec(), inputs.subject),
        budget * 35 / 100,
    );
    let (relations, omitted_relations) = bounded_records(
        &scoped_records(inputs.relation_context.to_vec(), inputs.subject),
        budget * 20 / 100,
    );
    let (neighborhood, omitted_neighborhood) = bounded_records(
        &scoped_records(inputs.neighborhood_context.to_vec(), inputs.subject),
        budget * 20 / 100,
    );
    let (parents, omitted_parents) = bounded_records(
        &scoped_records(
            inputs
                .parent_context
                .iter()
                .take(1)
                .cloned()
                .collect::<Vec<_>>(),
            inputs.subject,
        ),
        budget * 15 / 100,
    );
    let evidence_payload = json!({
        "source": source,
        "relations": relations,
        "neighborhood": neighborhood,
        "parents": parents,
        "token_budget": budget,
        "source_evidence_required": inputs.source_evidence_required
    });
    let evidence_digest = {
        use sha2::{Digest, Sha256};
        let digest = Sha256::digest(record_json(&evidence_payload).as_bytes());
        digest
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    };
    let frame_id = stable_id(
        "kogwistar_llm_wiki.maintenance_observation",
        &[
            inputs.subject.workspace_id.as_str(),
            inputs.subject.namespace.as_str(),
            inputs.subject.kind.as_str(),
            inputs.subject.subject_id.as_str(),
            inputs.subject.revision_id.as_deref().unwrap_or_default(),
            inputs
                .subject
                .parse_member_id
                .as_deref()
                .unwrap_or_default(),
            inputs.active_view_id.unwrap_or_default(),
            inputs
                .active_view_version
                .unwrap_or_default()
                .to_string()
                .as_str(),
            evidence_digest.as_str(),
        ],
    )
    .to_string();
    MaintenanceObservationFrame {
        frame_id,
        subject: inputs.subject.clone(),
        source_context: source,
        relation_context: relations,
        neighborhood_context: neighborhood,
        omitted_counts: BTreeMap::from([
            ("source".to_owned(), omitted_source),
            ("relations".to_owned(), omitted_relations),
            ("neighborhood".to_owned(), omitted_neighborhood),
            ("parent".to_owned(), omitted_parents),
        ]),
        parent_context: parents,
        token_budget: budget,
        source_evidence_required: inputs.source_evidence_required,
        active_view_id: inputs.active_view_id.map(str::to_owned),
        active_view_version: inputs.active_view_version,
    }
}

fn finding(
    code: &str,
    verdict: &str,
    severity: &str,
    subject_id: &str,
    evidence_ids: &[String],
    message: &str,
    recommended_action: Option<&str>,
) -> Value {
    json!({
        "code": code,
        "verdict": verdict,
        "severity": severity,
        "subject_id": subject_id,
        "evidence_ids": evidence_ids,
        "message": message,
        "recommended_action": recommended_action
    })
}

fn action_rank(action: &str) -> i64 {
    match action {
        "request_human_review" => 100,
        "reparse_region" => 90,
        "switch_to_boundary" => 80,
        "switch_to_excerpt" => 70,
        "retry_same_strategy" => 60,
        "expand_children" => 50,
        "propose_relation_patch" => 45,
        "validate_crosslinks" => 44,
        "review_parent" => 40,
        _ => 0,
    }
}

pub struct AssessmentInputs<'a> {
    pub frame: &'a MaintenanceObservationFrame,
    pub critic_failed: bool,
    pub critic_status: &'a str,
    pub critic_findings: &'a [Value],
    pub watermark_expiry_seconds: i64,
    pub now_epoch: i64,
}

#[allow(clippy::too_many_lines)]
pub fn assess_observation_frame(inputs: &AssessmentInputs<'_>) -> ParseAndGraphQualityAssessment {
    let frame = inputs.frame;
    let critic_failed = inputs.critic_failed
        || inputs.critic_status == "failed"
        || inputs.critic_status == "blocked_context";
    let mut findings = inputs.critic_findings.to_vec();
    let mut selected_action = Option::<(i64, String)>::None;
    let mut selected_verdict = Option::<(i64, String)>::None;
    let mut source_statuses = std::collections::BTreeSet::new();
    for item in &frame.source_context {
        let status = item
            .get("quality_status")
            .or_else(|| item.get("parse_status"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        if !status.is_empty() {
            source_statuses.insert(status.clone());
        }
        let (verdict, action) = match status.as_str() {
            "too_coarse" => ("too_coarse", "expand_children"),
            "too_fine" => ("too_fine", "switch_to_boundary"),
            "coverage_gap" => ("coverage_gap", "reparse_region"),
            "overlap" => ("overlap", "retry_same_strategy"),
            "duplicate" => ("duplicate", "review_parent"),
            _ => continue,
        };
        let rank = action_rank(action);
        if selected_action
            .as_ref()
            .is_none_or(|selected| rank > selected.0)
        {
            selected_action = Some((rank, action.to_owned()));
            selected_verdict = Some((rank, verdict.to_owned()));
        }
        findings.push(finding(
            &format!("parse_quality_{status}"),
            verdict,
            "warning",
            frame.subject.subject_id.as_str(),
            &[item
                .get("member_id")
                .or_else(|| item.get("evidence_id"))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned()],
            &format!("source interpretation is explicitly classified as {status}"),
            None,
        ));
    }
    let verified_source_present = frame.source_context.iter().any(|item| {
        item.get("evidence_role") == Some(&Value::from("authoritative_source"))
            && item.get("source_evidence_status") == Some(&Value::from("span_verified"))
    });
    if frame.source_evidence_required && !verified_source_present {
        findings.push(finding(
            "source_evidence_unavailable",
            "review_required",
            "error",
            frame.subject.subject_id.as_str(),
            std::slice::from_ref(&frame.subject.subject_id),
            "No digest-verified source span is available for this assessment.",
            Some("request_human_review"),
        ));
        selected_action = Some((100, "request_human_review".to_owned()));
        selected_verdict = Some((100, "review_required".to_owned()));
    }
    for finding_value in &findings {
        let action = finding_value
            .get("recommended_action")
            .and_then(Value::as_str)
            .unwrap_or("none");
        if action == "none" {
            continue;
        }
        let rank = action_rank(action);
        if selected_action
            .as_ref()
            .is_none_or(|selected| rank > selected.0)
        {
            selected_action = Some((rank, action.to_owned()));
            selected_verdict = Some((
                rank,
                finding_value
                    .get("verdict")
                    .and_then(Value::as_str)
                    .unwrap_or("quality_unknown")
                    .to_owned(),
            ));
        }
    }
    let uncertain_statuses = [
        "expanding",
        "quality_unknown",
        "review_required",
        "stale",
        "failed",
        "inactive",
        "historical",
        "unknown",
    ];
    if critic_failed
        || source_statuses
            .iter()
            .any(|status| uncertain_statuses.contains(&status.as_str()))
    {
        let evidence_ids = frame
            .source_context
            .iter()
            .filter_map(|item| {
                item.get("member_id")
                    .or_else(|| item.get("evidence_id"))
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            })
            .collect::<Vec<_>>();
        findings.push(finding(
            "parse_quality_uncertain",
            if critic_failed {
                "quality_unknown"
            } else {
                "review_required"
            },
            "error",
            frame.subject.subject_id.as_str(),
            &evidence_ids,
            "source interpretation is not safe for automatic graph repair",
            None,
        ));
    }
    for relation in &frame.relation_context {
        let has_provenance = relation.get("provenance").is_some_and(Value::is_object);
        let has_evidence = relation
            .get("evidence_ids")
            .and_then(Value::as_array)
            .is_some_and(|values| !values.is_empty());
        if !has_provenance && !has_evidence {
            findings.push(finding(
                "relation_missing_provenance",
                "relation_unsupported",
                "warning",
                relation
                    .get("relation_id")
                    .and_then(Value::as_str)
                    .unwrap_or(frame.subject.subject_id.as_str()),
                &[],
                "relation has no auditable evidence reference",
                None,
            ));
        }
    }
    if findings.is_empty() {
        let verdict = "adequate";
        let action = "none";
        let watermark_key = watermark_key(
            frame,
            verdict,
            "deterministic",
            inputs.now_epoch / inputs.watermark_expiry_seconds.max(60),
        );
        return ParseAndGraphQualityAssessment {
            assessment_id: assessment_identity(
                frame,
                verdict,
                action,
                &findings,
                watermark_key.as_str(),
                inputs.critic_status,
                false,
                inputs.watermark_expiry_seconds,
            ),
            watermark_key,
            frame_id: frame.frame_id.clone(),
            subject: frame.subject.clone(),
            verdict: verdict.to_owned(),
            recommended_action: action.to_owned(),
            findings: Vec::new(),
            active_view_id: frame.active_view_id.clone(),
            active_view_version: frame.active_view_version,
            evaluator_version: "maintenance-observation-v1".to_owned(),
            critic_status: inputs.critic_status.to_owned(),
            continuation_allowed: false,
            watermark_expiry_seconds: inputs.watermark_expiry_seconds.max(60),
        };
    }
    let verdict = if critic_failed {
        "quality_unknown"
    } else {
        selected_verdict
            .as_ref()
            .map(|(_, value)| value.as_str())
            .unwrap_or("review_required")
    };
    let action = if critic_failed {
        "request_human_review"
    } else {
        selected_action
            .as_ref()
            .map(|(_, value)| value.as_str())
            .unwrap_or("review_parent")
    };
    let watermark_key = watermark_key(
        frame,
        verdict,
        if critic_failed {
            "critic_failed"
        } else {
            "deterministic"
        },
        inputs.now_epoch / inputs.watermark_expiry_seconds.max(60),
    );
    ParseAndGraphQualityAssessment {
        assessment_id: assessment_identity(
            frame,
            verdict,
            action,
            &findings,
            watermark_key.as_str(),
            inputs.critic_status,
            !critic_failed,
            inputs.watermark_expiry_seconds,
        ),
        watermark_key,
        frame_id: frame.frame_id.clone(),
        subject: frame.subject.clone(),
        verdict: verdict.to_owned(),
        recommended_action: action.to_owned(),
        findings,
        active_view_id: frame.active_view_id.clone(),
        active_view_version: frame.active_view_version,
        evaluator_version: "maintenance-observation-v1".to_owned(),
        critic_status: inputs.critic_status.to_owned(),
        continuation_allowed: !critic_failed,
        watermark_expiry_seconds: inputs.watermark_expiry_seconds.max(60),
    }
}

fn watermark_key(
    frame: &MaintenanceObservationFrame,
    verdict: &str,
    critic_state: &str,
    watermark_epoch: i64,
) -> String {
    stable_id(
        "maintenance_quality_watermark",
        &[
            frame.subject.workspace_id.as_str(),
            frame.subject.namespace.as_str(),
            frame.subject.subject_id.as_str(),
            frame.subject.revision_id.as_deref().unwrap_or_default(),
            frame.active_view_id.as_deref().unwrap_or_default(),
            frame
                .active_view_version
                .unwrap_or_default()
                .to_string()
                .as_str(),
            verdict,
            critic_state,
            watermark_epoch.to_string().as_str(),
        ],
    )
    .to_string()
}

#[allow(clippy::too_many_arguments)]
fn assessment_identity(
    frame: &MaintenanceObservationFrame,
    verdict: &str,
    recommended_action: &str,
    findings: &[Value],
    watermark_key: &str,
    critic_status: &str,
    continuation_allowed: bool,
    watermark_expiry_seconds: i64,
) -> String {
    let payload = json!({
        "frame_id": frame.frame_id,
        "verdict": verdict,
        "recommended_action": recommended_action,
        "findings": findings,
        "active_view_id": frame.active_view_id,
        "active_view_version": frame.active_view_version,
        "watermark_key": watermark_key,
        "critic_status": critic_status,
        "continuation_allowed": continuation_allowed,
        "watermark_expiry_seconds": watermark_expiry_seconds
    });
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(record_json(&payload).as_bytes());
    let digest_hex = digest
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    stable_id("maintenance_assessment", &[digest_hex.as_str()]).to_string()
}
