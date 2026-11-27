// T5.7 disambiguation contracts - mirroring Python disambiguation_contracts.py
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

/// Mirrors Python DisambiguationArtifactStatus.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum DisambiguationArtifactStatus {
    #[default]
    Pending,
    Resolved,
    Superseded,
    Stale,
    InsufficientEvidence,
    NeedsMoreEvidenceCollection,
    Challenged,
}

/// Mirrors Python DisambiguationResolutionSource.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum DisambiguationResolutionSource {
    #[default]
    None,
    User,
    Policy,
    NewEvidence,
    PriorDecision,
}

/// Mirrors Python DisambiguationDecisionKind.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum DisambiguationDecisionKind {
    SameEntity,
    DistinctEntities,
    #[default]
    Ambiguous,
    IllFormedCandidate,
    InsufficientEvidence,
}

/// Mirrors Python DisambiguationScoreBundle.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, Default)]
pub struct DisambiguationScoreBundle {
    pub merge_likelihood: f64,
    pub distinction_pressure: f64,
    pub review_priority: f64,
    pub usage_frequency: u64,
    pub answer_risk: f64,
}

/// Mirrors Python DisambiguationCandidate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DisambiguationCandidate {
    pub artifact_id: String,
    pub workspace_id: String,
    pub candidate_key: String,
    pub entity_ids: Vec<String>,
    pub evidence_snapshot_id: String,
    pub evidence_cutoff_ms: i64,
    pub last_reconciled_evidence_version: i64,
    pub artifact_status: DisambiguationArtifactStatus,
    pub resolution_source: DisambiguationResolutionSource,
    pub semantic_decision: DisambiguationDecisionKind,
    pub question: String,
    pub question_is_concrete: bool,
    pub evidence_summary: String,
    pub canonical_entity_id: Option<String>,
    pub related_entity_ids: Vec<String>,
    pub replacement_question: Option<String>,
    pub source_document_ids: Vec<String>,
    pub source_span_ids: Vec<String>,
    pub score_bundle: DisambiguationScoreBundle,
    pub metadata: BTreeMap<String, Value>,
}

/// Mirrors Python DisambiguationEvidenceUpdate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, Default)]
pub struct DisambiguationEvidenceUpdate {
    pub evidence_version: i64,
    pub question_is_concrete: bool,
    pub resolves_equivalence: bool,
    pub resolves_distinction: bool,
    pub user_decision: Option<DisambiguationDecisionKind>,
    pub policy_decision: Option<DisambiguationDecisionKind>,
    pub candidate_group_size: Option<u64>,
    pub replacement_question: Option<String>,
    pub evidence_summary: Option<String>,
    pub missing_evidence: Vec<String>,
    pub merge_likelihood: Option<f64>,
    pub distinction_pressure: Option<f64>,
    pub review_priority: Option<f64>,
    pub decision_challenged: bool,
    pub challenge_reason: Option<String>,
}

/// Mirrors Python DisambiguationReconciliationResult.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DisambiguationReconciliationResult {
    pub candidate: DisambiguationCandidate,
    pub status: DisambiguationArtifactStatus,
    pub resolution_source: DisambiguationResolutionSource,
    pub semantic_decision: DisambiguationDecisionKind,
    pub should_surface_to_user: bool,
    pub reason: String,
    pub new_evidence_version: i64,
    pub replacement_question: Option<String>,
    pub challenge_required: bool,
}

impl DisambiguationCandidate {
    /// Mirrors Python _validate_candidate model validator.
    pub fn validate(&self) -> Result<(), String> {
        let entity_ids: Vec<&str> = self
            .entity_ids
            .iter()
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .collect();
        if entity_ids.len() < 2 {
            return Err("disambiguation candidate requires at least two entity_ids".to_owned());
        }
        let mut seen = BTreeMap::new();
        for e in &entity_ids {
            if seen.insert(*e, ()).is_some() {
                return Err("disambiguation candidate entity_ids must be unique".to_owned());
            }
        }
        if self.question.trim().is_empty() {
            return Err("disambiguation candidate requires a non-empty question".to_owned());
        }
        if let Some(canonical) = &self.canonical_entity_id {
            let canonical = canonical.trim();
            if !canonical.is_empty() && !entity_ids.contains(&canonical) {
                return Err("canonical_entity_id must be one of entity_ids".to_owned());
            }
        }
        let related: Vec<&str> = self
            .related_entity_ids
            .iter()
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .collect();
        if !related.is_empty() && related.iter().any(|e| !entity_ids.contains(e)) {
            return Err("related_entity_ids must be a subset of entity_ids".to_owned());
        }
        Ok(())
    }

    pub fn wire_name() -> &'static str {
        "disambiguation_candidate"
    }
}

impl DisambiguationEvidenceUpdate {
    /// Mirrors Python _validate_resolution_flags model validator.
    pub fn validate(&self) -> Result<(), String> {
        if self.resolves_equivalence && self.resolves_distinction {
            return Err(
                "an evidence update cannot resolve equivalence and distinction at the same time"
                    .to_owned(),
            );
        }
        if self.user_decision.is_some() && self.policy_decision.is_some() {
            return Err(
                "an evidence update can only carry one explicit decision source".to_owned(),
            );
        }
        Ok(())
    }
}

impl DisambiguationScoreBundle {
    pub fn wire_name() -> &'static str {
        "disambiguation_score_bundle"
    }
}
