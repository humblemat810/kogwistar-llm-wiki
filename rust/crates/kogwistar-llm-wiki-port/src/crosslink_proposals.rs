use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrosslinkEvidence {
    pub evidence_id: String,
    pub node_id: String,
    pub source_document_id: String,
    pub source_revision_id: String,
    pub revision_document_id: String,
    pub source_digest: String,
    pub start_char: i64,
    pub end_char: i64,
    pub excerpt: String,
}

impl CrosslinkEvidence {
    pub fn validate(&self) -> Result<(), String> {
        if self.evidence_id.is_empty() || self.evidence_id.len() > 160 {
            return Err("evidence_id must be between 1 and 160 characters".to_owned());
        }
        if self.node_id.is_empty() || self.node_id.len() > 512 {
            return Err("node_id must be between 1 and 512 characters".to_owned());
        }
        if self.source_document_id.is_empty() || self.source_document_id.len() > 512 {
            return Err("source_document_id must be between 1 and 512 characters".to_owned());
        }
        if self.source_revision_id.is_empty() || self.source_revision_id.len() > 512 {
            return Err("source_revision_id must be between 1 and 512 characters".to_owned());
        }
        if self.revision_document_id.is_empty() || self.revision_document_id.len() > 512 {
            return Err("revision_document_id must be between 1 and 512 characters".to_owned());
        }
        if self.source_digest.len() != 64
            || !self
                .source_digest
                .chars()
                .all(|character| character.is_ascii_hexdigit() && !character.is_ascii_uppercase())
        {
            return Err("source_digest must be a lowercase SHA-256 digest".to_owned());
        }
        if self.start_char < 0 {
            return Err("start_char must be non-negative".to_owned());
        }
        if self.end_char <= 0 {
            return Err("end_char must be positive".to_owned());
        }
        if self.end_char <= self.start_char {
            return Err("evidence end_char must be greater than start_char".to_owned());
        }
        if self.excerpt.is_empty() || self.excerpt.len() > 1200 {
            return Err("excerpt must be between 1 and 1200 characters".to_owned());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrosslinkProposalOperation {
    pub left_evidence_id: String,
    pub right_evidence_id: String,
    pub relation: String,
    pub rationale: String,
    pub supersedes_edge_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrosslinkProposalGroup {
    pub group_id: String,
    pub rationale: String,
    #[serde(default)]
    pub indivisible: bool,
    pub operations: Vec<CrosslinkProposalOperation>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrosslinkProposalResponse {
    #[serde(default)]
    pub groups: Vec<CrosslinkProposalGroup>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrosslinkCriticResponse {
    pub verdict: String,
    pub explanation: String,
    pub evidence_ids: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrosslinkReviewDecision {
    pub artifact_id: String,
    pub decision: String,
    pub expected_version: i64,
}

pub fn validate_crosslink_proposal_response(
    response: &CrosslinkProposalResponse,
) -> Result<(), String> {
    if response.groups.len() > 12 {
        return Err("a proposal may contain at most 12 groups".to_owned());
    }
    let mut group_ids = Vec::new();
    for group in &response.groups {
        if group.group_id.is_empty() || group.group_id.len() > 160 {
            return Err("group_id must be between 1 and 160 characters".to_owned());
        }
        if group.rationale.is_empty() || group.rationale.len() > 1000 {
            return Err("group rationale must be between 1 and 1000 characters".to_owned());
        }
        if group.operations.is_empty() || group.operations.len() > 12 {
            return Err("a group must contain 1 to 12 operations".to_owned());
        }
        let mut targets = Vec::new();
        for operation in &group.operations {
            if operation.left_evidence_id.is_empty() || operation.left_evidence_id.len() > 160 {
                return Err("left_evidence_id must be between 1 and 160 characters".to_owned());
            }
            if operation.right_evidence_id.is_empty() || operation.right_evidence_id.len() > 160 {
                return Err("right_evidence_id must be between 1 and 160 characters".to_owned());
            }
            if operation.relation.is_empty() || operation.relation.len() > 120 {
                return Err("relation must be between 1 and 120 characters".to_owned());
            }
            if operation.rationale.is_empty() || operation.rationale.len() > 600 {
                return Err("operation rationale must be between 1 and 600 characters".to_owned());
            }
            if let Some(target) = &operation.supersedes_edge_id {
                if target.is_empty() || target.len() > 512 {
                    return Err(
                        "supersedes_edge_id must be between 1 and 512 characters".to_owned()
                    );
                }
                targets.push(target.clone());
            }
        }
        let unique_targets = targets
            .iter()
            .cloned()
            .collect::<std::collections::BTreeSet<_>>();
        if unique_targets.len() != targets.len() {
            return Err("a group may supersede each edge at most once".to_owned());
        }
        group_ids.push(group.group_id.clone());
    }
    let unique_group_ids = group_ids
        .iter()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    if unique_group_ids.len() != group_ids.len() {
        return Err("group_id values must be unique within a proposal".to_owned());
    }
    Ok(())
}

pub fn validate_crosslink_critic_response(
    response: &CrosslinkCriticResponse,
) -> Result<(), String> {
    if !matches!(response.verdict.as_str(), "approve" | "reject" | "review") {
        return Err("verdict must be approve, reject, or review".to_owned());
    }
    if response.explanation.is_empty() || response.explanation.len() > 1000 {
        return Err("explanation must be between 1 and 1000 characters".to_owned());
    }
    if response.evidence_ids.is_empty() || response.evidence_ids.len() > 24 {
        return Err("evidence_ids must contain 1 to 24 entries".to_owned());
    }
    Ok(())
}

pub fn validate_crosslink_review_decision(
    decision: &CrosslinkReviewDecision,
) -> Result<(), String> {
    if decision.artifact_id.is_empty() {
        return Err("artifact_id must not be empty".to_owned());
    }
    if !matches!(decision.decision.as_str(), "approve" | "reject") {
        return Err("decision must be approve or reject".to_owned());
    }
    if decision.expected_version < 1 {
        return Err("expected_version must be at least 1".to_owned());
    }
    Ok(())
}

#[must_use]
pub fn crosslink_patch_requires_atomic_replacement(
    requires_atomic_group: bool,
    operations: &[Value],
) -> bool {
    requires_atomic_group
        || operations
            .iter()
            .filter_map(|operation| operation.get("supersedes_ids"))
            .filter_map(Value::as_array)
            .any(|values| !values.is_empty())
}
