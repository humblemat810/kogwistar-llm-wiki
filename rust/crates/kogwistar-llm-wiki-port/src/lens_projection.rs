//! Bounded lens projections: project a graph slice within lens budgets.
//! Mirrors `kogwistar_llm_wiki.workbench.semantic_lens`; nodes/edges are
//! caller-supplied (already authorised reads), so this module never becomes
//! graph truth. Only the request validation, projection bounds, and proposal
//! validation are ported.

use serde_json::Value;
use sha2::{Digest, Sha256};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Default)]
pub enum RetrievalMode {
    #[default]
    Auto,
    Graph,
    Semantic,
    Flat,
}

impl RetrievalMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            RetrievalMode::Auto => "auto",
            RetrievalMode::Graph => "graph",
            RetrievalMode::Semantic => "semantic",
            RetrievalMode::Flat => "flat",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SemanticLensRequest {
    pub workspace_id: String,
    pub graph_spaces: Vec<String>,
    pub query_text: String,
    pub semantic_retrieval: bool,
    pub retrieval_mode: RetrievalMode,
    pub retrieval_required: bool,
    pub similarity_threshold: Option<f64>,
    pub source_evidence_required: bool,
    pub explicit_anchor_ids: Vec<String>,
    pub hop_limit: u32,
    pub max_nodes: usize,
    pub max_edges: usize,
    pub max_hyperedges: usize,
    pub pinned_node_ids: Vec<String>,
    pub source_watermark: Value,
    pub include_tombstones: bool,
}

#[derive(Debug, Eq, PartialEq)]
pub enum LensRequestError {
    EmptyWorkspaceId,
    EmptyGraphSpaces,
    SimilarityThresholdRequiresSemanticOrFlat,
    SourceEvidenceRequiresFlat,
    SimilarityThresholdMustNotBeNan,
    HopLimitOutOfRange,
    PinnedNodesExceedMaxNodes,
}

impl SemanticLensRequest {
    /// Mirror `SemanticLensRequest.__post_init__` validation.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        workspace_id: impl Into<String>,
        graph_spaces: impl IntoIterator<Item = impl Into<String>>,
        retrieval_mode: RetrievalMode,
        max_nodes: usize,
        similarity_threshold: Option<f64>,
        source_evidence_required: bool,
        hop_limit: u32,
        pinned_node_ids: Vec<String>,
        source_watermark: Value,
    ) -> Result<Self, LensRequestError> {
        let workspace_id = workspace_id.into();
        if workspace_id.trim().is_empty() {
            return Err(LensRequestError::EmptyWorkspaceId);
        }
        let graph_spaces: Vec<String> = graph_spaces.into_iter().map(|s| s.into()).collect();
        if graph_spaces.is_empty() {
            return Err(LensRequestError::EmptyGraphSpaces);
        }
        if let Some(threshold) = similarity_threshold {
            if threshold.is_nan() {
                return Err(LensRequestError::SimilarityThresholdMustNotBeNan);
            }
            if retrieval_mode == RetrievalMode::Graph {
                return Err(LensRequestError::SimilarityThresholdRequiresSemanticOrFlat);
            }
        }
        if retrieval_mode != RetrievalMode::Flat && source_evidence_required {
            return Err(LensRequestError::SourceEvidenceRequiresFlat);
        }
        if hop_limit > 8 {
            return Err(LensRequestError::HopLimitOutOfRange);
        }
        if max_nodes < 1 {
            return Err(LensRequestError::PinnedNodesExceedMaxNodes);
        }
        let pinned_count: usize = pinned_node_ids
            .iter()
            .cloned()
            .collect::<std::collections::HashSet<_>>()
            .len();
        if pinned_count > max_nodes {
            return Err(LensRequestError::PinnedNodesExceedMaxNodes);
        }
        Ok(Self {
            workspace_id,
            graph_spaces,
            query_text: String::new(),
            semantic_retrieval: false,
            retrieval_mode,
            retrieval_required: false,
            similarity_threshold,
            source_evidence_required,
            explicit_anchor_ids: Vec::new(),
            hop_limit,
            max_nodes,
            max_edges: 0,
            max_hyperedges: 0,
            pinned_node_ids,
            source_watermark,
            include_tombstones: false,
        })
    }
}

#[must_use]
pub fn lens_id(request: &SemanticLensRequest) -> String {
    let mut hasher = Sha256::new();
    hasher.update(request.workspace_id.as_bytes());
    for space in &request.graph_spaces {
        hasher.update(space.as_bytes());
    }
    hasher.update(request.query_text.as_bytes());
    let digest = hasher.finalize();
    let hex: String = digest.iter().take(12).map(|b| format!("{b:02x}")).collect();
    format!("lens:{hex}")
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LensNode {
    pub id: String,
    pub graph_space: String,
    pub namespace: String,
    pub label: String,
    pub node_type: String,
    pub entity_revision: Option<Value>,
    pub metadata: Value,
    pub grounding: Vec<Value>,
    pub payload: Value,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LensEdge {
    pub id: String,
    pub graph_space: String,
    pub namespace: String,
    pub source_ids: Vec<String>,
    pub target_ids: Vec<String>,
    pub relation: String,
    pub entity_revision: Option<Value>,
    pub metadata: Value,
    pub grounding: Vec<Value>,
    pub payload: Value,
}

#[must_use]
pub fn is_hyperedge(edge: &LensEdge) -> bool {
    edge.source_ids.len() + edge.target_ids.len() > 2
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LensParticipation {
    pub edge_id: String,
    pub node_id: String,
    pub role: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SelectionExplanation {
    pub node_id: String,
    pub reason: String,
    pub score: f64,
    pub matched_terms: Vec<String>,
    pub distance: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RetrievalStatus {
    pub mode: String,
    pub semantic_available: bool,
    pub backend: Option<String>,
    pub metric: Option<String>,
    pub degraded_reason: Option<String>,
    pub score_kind: String,
}

#[must_use]
pub fn default_retrieval_status() -> RetrievalStatus {
    RetrievalStatus {
        mode: "graph".to_owned(),
        semantic_available: false,
        backend: None,
        metric: None,
        degraded_reason: None,
        score_kind: "similarity".to_owned(),
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SemanticLensSnapshot {
    pub lens_id: String,
    pub workspace_id: String,
    pub source_watermark: Value,
    pub projected_at_ms: u64,
    pub completeness: String,
    pub nodes: Vec<LensNode>,
    pub edges: Vec<LensEdge>,
    pub hyperedges: Vec<LensEdge>,
    pub participations: Vec<LensParticipation>,
    pub anchor_explanations: Vec<SelectionExplanation>,
    pub selection_explanations: Vec<SelectionExplanation>,
    pub omitted_summary: Value,
    pub query_timing_ms: u64,
    pub retrieval: RetrievalStatus,
    pub flat_hits: Vec<Value>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProposalValidation {
    pub accepted: bool,
    pub requires_confirmation: bool,
    pub reason: String,
    pub target_ids: Vec<String>,
}

#[must_use]
pub fn validate_edit_proposal(
    snapshot: &SemanticLensSnapshot,
    proposal: &Value,
) -> ProposalValidation {
    let proposal_lens_id = proposal
        .get("lens_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if proposal_lens_id != snapshot.lens_id {
        return rejected("stale_lens_id", vec![]);
    }
    if proposal.get("source_watermark") != Some(&snapshot.source_watermark) {
        return rejected("stale_source_watermark", vec![]);
    }
    let target_ids: Vec<String> = string_ids(proposal.get("target_ids"));
    let visible_ids: Vec<String> = snapshot
        .nodes
        .iter()
        .map(|n| n.id.clone())
        .chain(snapshot.edges.iter().map(|e| e.id.clone()))
        .chain(snapshot.hyperedges.iter().map(|e| e.id.clone()))
        .collect();
    let missing: Vec<String> = target_ids
        .iter()
        .filter(|id| !visible_ids.contains(*id))
        .cloned()
        .collect();
    if !missing.is_empty() {
        return rejected("target_not_in_scoped_lens", target_ids);
    }
    let evidence_ids: Vec<String> = string_ids(proposal.get("evidence_ids"));
    let operation = proposal
        .get("operation")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if evidence_ids.is_empty() && operation != "no_change" {
        return rejected("grounding_evidence_required", target_ids);
    }
    let missing_evidence: Vec<String> = evidence_ids
        .iter()
        .filter(|id| !visible_ids.contains(*id))
        .cloned()
        .collect();
    if !missing_evidence.is_empty() {
        return rejected("evidence_not_in_scoped_lens", target_ids);
    }
    if let Some(expected) = proposal
        .get("expected_revisions")
        .and_then(|v| v.as_object())
    {
        let mut revisions: Value = Value::Object(
            snapshot
                .nodes
                .iter()
                .map(|n| {
                    (
                        n.id.clone(),
                        n.entity_revision.clone().unwrap_or(Value::Null),
                    )
                })
                .collect(),
        );
        if let Value::Object(map) = &mut revisions {
            for edge in &snapshot.edges {
                map.insert(
                    edge.id.clone(),
                    edge.entity_revision.clone().unwrap_or(Value::Null),
                );
            }
            for edge in &snapshot.hyperedges {
                map.insert(
                    edge.id.clone(),
                    edge.entity_revision.clone().unwrap_or(Value::Null),
                );
            }
        }
        for (node_id, expected) in expected {
            if revisions.get(node_id) != Some(expected) {
                return rejected("stale_entity_revision", target_ids);
            }
        }
    }
    ProposalValidation {
        accepted: true,
        requires_confirmation: true,
        reason: "ready_for_explicit_confirmation".to_owned(),
        target_ids,
    }
}

#[must_use]
fn rejected(reason: &str, target_ids: Vec<String>) -> ProposalValidation {
    ProposalValidation {
        accepted: false,
        requires_confirmation: true,
        reason: reason.to_owned(),
        target_ids,
    }
}

#[must_use]
fn string_ids(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_owned()))
                .collect()
        })
        .unwrap_or_default()
}

#[must_use]
pub fn project_bounded(
    request: &SemanticLensRequest,
    nodes: Vec<LensNode>,
    edges: Vec<LensEdge>,
    now_ms: u64,
) -> SemanticLensSnapshot {
    let nodes_omitted = nodes.len().saturating_sub(request.max_nodes);
    let edges_omitted = edges.len().saturating_sub(request.max_edges);
    let mut nodes = nodes;
    nodes.truncate(request.max_nodes);
    let mut edges = edges;
    edges.truncate(request.max_edges);
    let hyperedges: Vec<LensEdge> = edges.iter().filter(|e| is_hyperedge(e)).cloned().collect();
    let edges: Vec<LensEdge> = edges.into_iter().filter(|e| !is_hyperedge(e)).collect();
    let completeness = if nodes_omitted == 0 && edges_omitted == 0 {
        "complete".to_owned()
    } else {
        "bounded".to_owned()
    };
    SemanticLensSnapshot {
        lens_id: lens_id(request),
        workspace_id: request.workspace_id.clone(),
        source_watermark: request.source_watermark.clone(),
        projected_at_ms: now_ms,
        completeness,
        nodes,
        edges,
        hyperedges,
        participations: Vec::new(),
        anchor_explanations: Vec::new(),
        selection_explanations: Vec::new(),
        omitted_summary: serde_json::json!({
            "nodes_omitted": nodes_omitted,
            "edges_omitted": edges_omitted,
        }),
        query_timing_ms: 0,
        retrieval: default_retrieval_status(),
        flat_hits: Vec::new(),
    }
}
