//! Cockpit action validation and bounded investigation loop for the workbench.
//! Mirrors `kogwistar_llm_wiki.workbench.workbench_cockpit`; every proposal
//! stays non-authoritative until an explicit user confirmation.

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};

use crate::lens_projection::{validate_edit_proposal, SemanticLensRequest, SemanticLensSnapshot};
use crate::maintenance_patches::{
    validate_maintenance_patch, MaintenanceOperationKind, MaintenancePatch, MaintenanceScopeKind,
};

pub const MAX_COCKPIT_PATCH_OPERATIONS: u32 = 12;
pub const MAX_COCKPIT_QUERY_TEXT: usize = 500;
pub const MAX_COCKPIT_ENTITY_IDS: usize = 12;
pub const MAX_COCKPIT_CITED_IDS: usize = 24;
pub const MAX_COCKPIT_RATIONALE: usize = 2_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CockpitActionKind {
    Answer,
    NoChange,
    RequestClarification,
    ResolveLens,
    InspectEvidence,
    QueryHistory,
    ProposePatch,
}

impl CockpitActionKind {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Answer => "answer",
            Self::NoChange => "no_change",
            Self::RequestClarification => "request_clarification",
            Self::ResolveLens => "resolve_lens",
            Self::InspectEvidence => "inspect_evidence",
            Self::QueryHistory => "query_history",
            Self::ProposePatch => "propose_patch",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CockpitAction {
    pub kind: CockpitActionKind,
    pub answer: Option<String>,
    pub query_text: Option<String>,
    pub entity_ids: Vec<String>,
    pub patch: Option<MaintenancePatch>,
    pub cited_entity_ids: Vec<String>,
    pub rationale: String,
}

impl CockpitAction {
    /// Mirror `CockpitAction._shape_matches_action` plus the bounded field
    /// limits of the Python pydantic contract.
    pub fn validate(&self) -> Result<(), String> {
        if self.query_text.as_deref().unwrap_or("").len() > MAX_COCKPIT_QUERY_TEXT {
            return Err("query_text exceeds 500 characters".to_owned());
        }
        if self.entity_ids.len() > MAX_COCKPIT_ENTITY_IDS {
            return Err("entity_ids exceeds 12 entries".to_owned());
        }
        if self.cited_entity_ids.len() > MAX_COCKPIT_CITED_IDS {
            return Err("cited_entity_ids exceeds 24 entries".to_owned());
        }
        if self.rationale.len() > MAX_COCKPIT_RATIONALE {
            return Err("rationale exceeds 2000 characters".to_owned());
        }
        match self.kind {
            CockpitActionKind::ProposePatch => {
                let patch = self
                    .patch
                    .as_ref()
                    .ok_or_else(|| "propose_patch requires patch".to_owned())?;
                if patch.operations.is_empty()
                    || patch.operations.len() > MAX_COCKPIT_PATCH_OPERATIONS as usize
                {
                    return Err("propose_patch requires 1..=12 operations".to_owned());
                }
            }
            _ => {
                if self.patch.is_some() {
                    return Err("patch is only allowed for propose_patch".to_owned());
                }
            }
        }
        if matches!(
            self.kind,
            CockpitActionKind::Answer | CockpitActionKind::RequestClarification
        ) && self.answer.as_deref().unwrap_or("").trim().is_empty()
        {
            return Err(format!("{} requires answer", self.kind.as_str()));
        }
        if self.kind == CockpitActionKind::ResolveLens
            && self.query_text.as_deref().unwrap_or("").trim().is_empty()
        {
            return Err("resolve_lens requires query_text".to_owned());
        }
        if self.kind == CockpitActionKind::InspectEvidence && self.entity_ids.is_empty() {
            return Err("inspect_evidence requires entity_ids".to_owned());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum CockpitTurnOutcome {
    Answer,
    NoChange,
    RequestClarification,
    Proposal,
}

impl CockpitTurnOutcome {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Answer => "answer",
            Self::NoChange => "no_change",
            Self::RequestClarification => "request_clarification",
            Self::Proposal => "proposal",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CockpitObservation {
    pub kind: String,
    pub summary: String,
    pub payload: Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CockpitTurnResult {
    pub answer: String,
    pub outcome: CockpitTurnOutcome,
    pub cited_entity_ids: Vec<String>,
    pub proposal: Option<Value>,
    pub trace: Vec<Value>,
    pub observations: Vec<Value>,
    pub lens: Value,
    pub final_request: Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CockpitLimits {
    pub max_actions: u32,
    pub max_history_records: u32,
}

impl Default for CockpitLimits {
    fn default() -> Self {
        Self {
            max_actions: 4,
            max_history_records: 8,
        }
    }
}

impl CockpitLimits {
    pub fn validate(&self) -> Result<(), String> {
        if !(1..=8).contains(&self.max_actions) {
            return Err("max_actions must be between 1 and 8".to_owned());
        }
        Ok(())
    }
}

pub trait LensResolver: Send + Sync {
    fn resolve(&self, request: &SemanticLensRequest) -> SemanticLensSnapshot;
}

pub trait HistoryQuery: Send + Sync {
    fn query_history(&self, workspace_id: &str, session_id: &str, limit: u32) -> Vec<Value>;
}

pub trait CockpitResponder: Send + Sync {
    fn respond(
        &self,
        request: &SemanticLensRequest,
        snapshot: &SemanticLensSnapshot,
        observations: &[CockpitObservation],
        progress: &dyn ProgressReporter,
    ) -> CockpitAction;
}

pub trait ProgressReporter: Send + Sync {
    fn report(&self);
}

pub struct WorkbenchCockpit {
    pub resolve_lens: Box<dyn LensResolver>,
    pub query_history: Box<dyn HistoryQuery>,
    pub limits: CockpitLimits,
    pub last_snapshot: Option<SemanticLensSnapshot>,
}

impl WorkbenchCockpit {
    /// Mirror `WorkbenchCockpit.run`: one bounded action per turn, proposals
    /// require explicit confirmation.
    pub fn run(
        &mut self,
        request: SemanticLensRequest,
        session_id: &str,
        responder: &dyn CockpitResponder,
        progress: &dyn ProgressReporter,
    ) -> CockpitTurnResult {
        self.limits
            .validate()
            .expect("cockpit limits must be valid");
        let mut snapshot = self.resolve_lens.resolve(&request);
        self.last_snapshot = Some(snapshot.clone());
        let mut current_request = request;
        let mut observations: Vec<CockpitObservation> = Vec::new();
        let mut trace: Vec<Value> = Vec::new();

        for action_index in 0..self.limits.max_actions {
            progress.report();
            let action = responder.respond(&current_request, &snapshot, &observations, progress);
            trace.push(json!({
                "action_index": action_index + 1,
                "kind": action.kind.as_str(),
                "rationale": action.rationale,
            }));

            match action.kind {
                CockpitActionKind::ResolveLens => {
                    let visible_node_ids: HashSet<&str> =
                        snapshot.nodes.iter().map(|n| n.id.as_str()).collect();
                    let invalid: Vec<String> = action
                        .entity_ids
                        .iter()
                        .filter(|id| !visible_node_ids.contains(id.as_str()))
                        .cloned()
                        .collect();
                    if !invalid.is_empty() {
                        trace.last_mut().unwrap().as_object_mut().unwrap().insert(
                            "rejected_anchor_ids".to_owned(),
                            Value::Array(
                                invalid.iter().map(|id| Value::String(id.clone())).collect(),
                            ),
                        );
                        observations.push(CockpitObservation {
                            kind: "resolve_lens_rejected".to_owned(),
                            summary: "rejected anchors outside the current lens".to_owned(),
                            payload: json!({ "invalid_anchor_ids": invalid }),
                        });
                        continue;
                    }
                    current_request = _bounded_follow_up_request(&current_request, &action);
                    snapshot = self.resolve_lens.resolve(&current_request);
                    self.last_snapshot = Some(snapshot.clone());
                    observations.push(CockpitObservation {
                        kind: "resolve_lens".to_owned(),
                        summary: "resolved a bounded follow-up lens".to_owned(),
                        payload: json!({
                            "lens_id": snapshot.lens_id,
                            "node_count": snapshot.nodes.len(),
                            "edge_count": snapshot.edges.len(),
                        }),
                    });
                    continue;
                }
                CockpitActionKind::InspectEvidence => {
                    let observation = _inspect_visible_entities(&snapshot, &action.entity_ids);
                    trace.last_mut().unwrap().as_object_mut().unwrap().insert(
                        "entity_ids".to_owned(),
                        Value::Array(
                            action
                                .entity_ids
                                .iter()
                                .map(|id| Value::String(id.clone()))
                                .collect(),
                        ),
                    );
                    observations.push(observation);
                    continue;
                }
                CockpitActionKind::QueryHistory => {
                    let records = self.query_history.query_history(
                        &current_request.workspace_id,
                        session_id,
                        self.limits.max_history_records,
                    );
                    observations.push(CockpitObservation {
                        kind: "query_history".to_owned(),
                        summary: format!("returned {} prior interactions", records.len()),
                        payload: json!({ "records": records }),
                    });
                    continue;
                }
                _ => {}
            }

            self.last_snapshot = Some(snapshot.clone());
            return _terminal_result(&action, &snapshot, trace, observations, &current_request);
        }

        self.last_snapshot = Some(snapshot.clone());
        CockpitTurnResult {
            answer: "I reached the bounded investigation-action limit without a safe conclusion."
                .to_owned(),
            outcome: CockpitTurnOutcome::NoChange,
            proposal: None,
            cited_entity_ids: Vec::new(),
            trace,
            observations: observation_values(&observations),
            lens: snapshot.to_dict(),
            final_request: _request_payload(&current_request),
        }
    }
}

fn _terminal_result(
    action: &CockpitAction,
    snapshot: &SemanticLensSnapshot,
    mut trace: Vec<Value>,
    observations: Vec<CockpitObservation>,
    current_request: &SemanticLensRequest,
) -> CockpitTurnResult {
    let visible_node_ids: HashSet<&str> = snapshot.nodes.iter().map(|n| n.id.as_str()).collect();
    let visible_edge_ids: HashSet<&str> = snapshot
        .edges
        .iter()
        .chain(snapshot.hyperedges.iter())
        .map(|e| e.id.as_str())
        .collect();
    let visible_ids: HashSet<&str> = visible_node_ids.union(&visible_edge_ids).cloned().collect();
    let cited: Vec<String> = action
        .cited_entity_ids
        .iter()
        .filter(|id| visible_ids.contains(id.as_str()))
        .cloned()
        .collect();

    if action.kind == CockpitActionKind::ProposePatch {
        let patch = action.patch.as_ref().expect("propose_patch requires patch");
        let target_ids = _patch_target_ids(patch);
        let mut proposal = Map::new();
        proposal.insert(
            "operation".to_owned(),
            Value::String("maintenance_patch".to_owned()),
        );
        proposal.insert(
            "lens_id".to_owned(),
            Value::String(snapshot.lens_id.clone()),
        );
        proposal.insert(
            "source_watermark".to_owned(),
            snapshot.source_watermark.clone(),
        );
        proposal.insert(
            "target_ids".to_owned(),
            Value::Array(
                target_ids
                    .iter()
                    .map(|id| Value::String(id.clone()))
                    .collect(),
            ),
        );
        proposal.insert(
            "evidence_ids".to_owned(),
            Value::Array(cited.iter().map(|id| Value::String(id.clone())).collect()),
        );
        proposal.insert(
            "expected_revisions".to_owned(),
            _expected_revisions(snapshot, &target_ids),
        );
        proposal.insert(
            "maintenance_patch".to_owned(),
            serde_json::to_value(patch).expect("maintenance patch serializes"),
        );
        let proposal_value = Value::Object(proposal);
        let (valid, reason) = validate_cockpit_proposal(snapshot, &proposal_value, None);
        trace.last_mut().unwrap().as_object_mut().unwrap().insert(
            "proposal_validation".to_owned(),
            Value::String(reason.clone()),
        );
        if !valid {
            return CockpitTurnResult {
                answer: format!("I could not produce a safely grounded graph proposal: {reason}"),
                outcome: CockpitTurnOutcome::NoChange,
                proposal: None,
                cited_entity_ids: cited,
                trace,
                observations: observation_values(&observations),
                lens: snapshot.to_dict(),
                final_request: _request_payload(current_request),
            };
        }
        return CockpitTurnResult {
            answer: action.answer.clone().unwrap_or_else(|| {
                "I prepared a grounded graph-change proposal for your review.".to_owned()
            }),
            outcome: CockpitTurnOutcome::Proposal,
            cited_entity_ids: cited,
            proposal: Some(proposal_value),
            trace,
            observations: observation_values(&observations),
            lens: snapshot.to_dict(),
            final_request: _request_payload(current_request),
        };
    }

    let outcome = match action.kind {
        CockpitActionKind::RequestClarification => CockpitTurnOutcome::RequestClarification,
        CockpitActionKind::NoChange => CockpitTurnOutcome::NoChange,
        _ => CockpitTurnOutcome::Answer,
    };
    CockpitTurnResult {
        answer: action.answer.clone().unwrap_or_else(|| {
            "No graph change is warranted from the current grounded evidence.".to_owned()
        }),
        outcome,
        proposal: None,
        cited_entity_ids: cited,
        trace,
        observations: observation_values(&observations),
        lens: snapshot.to_dict(),
        final_request: _request_payload(current_request),
    }
}

/// Mirror `validate_cockpit_proposal`: lens envelope, workspace scope, evidence
/// containment, per-operation grounding, and the maintenance-patch contract.
#[must_use]
pub fn validate_cockpit_proposal(
    snapshot: &SemanticLensSnapshot,
    proposal: &Value,
    expected_workspace_id: Option<&str>,
) -> (bool, String) {
    let raw_patch = match proposal.get("maintenance_patch") {
        Some(Value::Object(_)) => proposal.get("maintenance_patch").unwrap().clone(),
        _ => return (false, "maintenance_patch_required".to_owned()),
    };
    let patch: MaintenancePatch = match serde_json::from_value(raw_patch) {
        Ok(patch) => patch,
        Err(error) => return (false, format!("invalid_maintenance_patch: {error}")),
    };
    let workspace_id = expected_workspace_id.unwrap_or(snapshot.workspace_id.as_str());
    if patch.scope.workspace_id != workspace_id {
        return (false, "maintenance_scope_workspace_mismatch".to_owned());
    }
    if patch.scope.scope_kind != MaintenanceScopeKind::Workspace.as_str() {
        return (false, "cockpit_scope_not_supported".to_owned());
    }
    let envelope = validate_edit_proposal(snapshot, proposal);
    if !envelope.accepted {
        return (false, envelope.reason);
    }
    let visible_node_ids: HashSet<&str> = snapshot.nodes.iter().map(|n| n.id.as_str()).collect();
    let visible_edge_ids: HashSet<&str> = snapshot
        .edges
        .iter()
        .chain(snapshot.hyperedges.iter())
        .map(|e| e.id.as_str())
        .collect();
    let visible_ids: HashSet<&str> = visible_node_ids.union(&visible_edge_ids).cloned().collect();
    let evidence_ids: HashSet<String> = proposal
        .get("evidence_ids")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_owned()))
                .collect()
        })
        .unwrap_or_default();
    if evidence_ids.is_empty()
        || !evidence_ids
            .iter()
            .all(|id| visible_ids.contains(id.as_str()))
    {
        return (false, "evidence_not_in_scoped_lens".to_owned());
    }
    let evidence_documents = _evidence_document_ids(snapshot, &evidence_ids);
    for operation in &patch.operations {
        if matches!(
            operation.kind,
            MaintenanceOperationKind::Noop | MaintenanceOperationKind::RequestReview
        ) {
            continue;
        }
        let Some(provenance) = &operation.provenance else {
            return (
                false,
                format!("operation_missing_provenance:{}", operation.operation_id),
            );
        };
        let mut operation_documents: HashSet<String> = HashSet::new();
        if let Some(document_id) = &provenance.source_document_id {
            operation_documents.insert(document_id.clone());
        }
        for pointer in &provenance.source_pointers {
            if let Some(document_id) = pointer
                .get("doc_id")
                .or_else(|| pointer.get("source_document_id"))
                .and_then(|v| v.as_str())
            {
                if !document_id.is_empty() {
                    operation_documents.insert(document_id.to_owned());
                }
            }
        }
        if operation_documents.is_empty()
            || !operation_documents
                .iter()
                .any(|doc| evidence_documents.contains(doc))
        {
            return (
                false,
                format!(
                    "operation_grounding_not_in_evidence:{}",
                    operation.operation_id
                ),
            );
        }
    }
    let report = validate_maintenance_patch(
        &patch,
        &snapshot
            .nodes
            .iter()
            .map(|n| n.id.clone())
            .collect::<Vec<_>>(),
        &snapshot
            .edges
            .iter()
            .chain(snapshot.hyperedges.iter())
            .map(|e| e.id.clone())
            .collect::<Vec<_>>(),
    );
    if !report.valid {
        let codes: Vec<&str> = report
            .issues
            .iter()
            .map(|issue| issue.code.as_str())
            .collect();
        return (
            false,
            format!("maintenance_patch_invalid: {}", codes.join(",")),
        );
    }
    (true, "ready_for_explicit_confirmation".to_owned())
}

#[must_use]
fn _patch_target_ids(patch: &MaintenancePatch) -> Vec<String> {
    let mut ids: HashSet<String> = HashSet::new();
    for operation in &patch.operations {
        for id in [
            operation.target_id.as_deref(),
            operation.from_node_id.as_deref(),
            operation.to_node_id.as_deref(),
        ] {
            if let Some(id) = id.filter(|id| !id.is_empty()) {
                ids.insert(id.to_owned());
            }
        }
        if operation.kind == MaintenanceOperationKind::TombstoneNode {
            if let Some(id) = operation.node_id.as_deref() {
                if !id.is_empty() {
                    ids.insert(id.to_owned());
                }
            }
        }
        if operation.kind == MaintenanceOperationKind::TombstoneEdge {
            if let Some(id) = operation.edge_id.as_deref() {
                if !id.is_empty() {
                    ids.insert(id.to_owned());
                }
            }
        }
        for id in &operation.supersedes_ids {
            if !id.is_empty() {
                ids.insert(id.clone());
            }
        }
    }
    let mut ids: Vec<String> = ids.into_iter().collect();
    ids.sort();
    ids
}

#[must_use]
fn _expected_revisions(snapshot: &SemanticLensSnapshot, target_ids: &[String]) -> Value {
    let mut revisions: Map<String, Value> = Map::new();
    for node in &snapshot.nodes {
        revisions.insert(
            node.id.clone(),
            node.entity_revision.clone().unwrap_or(Value::Null),
        );
    }
    for edge in snapshot.edges.iter().chain(snapshot.hyperedges.iter()) {
        revisions.insert(
            edge.id.clone(),
            edge.entity_revision.clone().unwrap_or(Value::Null),
        );
    }
    Value::Object(
        target_ids
            .iter()
            .filter_map(|id| revisions.get(id).map(|value| (id.clone(), value.clone())))
            .collect(),
    )
}

#[must_use]
fn _request_payload(request: &SemanticLensRequest) -> Value {
    Map::from_iter([
        (
            "workspace_id".to_owned(),
            Value::String(request.workspace_id.clone()),
        ),
        (
            "graph_spaces".to_owned(),
            Value::Array(
                request
                    .graph_spaces
                    .iter()
                    .map(|s| Value::String(s.clone()))
                    .collect(),
            ),
        ),
        (
            "query_text".to_owned(),
            Value::String(request.query_text.clone()),
        ),
        (
            "semantic_retrieval".to_owned(),
            Value::Bool(request.semantic_retrieval),
        ),
        (
            "explicit_anchor_ids".to_owned(),
            Value::Array(
                request
                    .explicit_anchor_ids
                    .iter()
                    .map(|s| Value::String(s.clone()))
                    .collect(),
            ),
        ),
        (
            "hop_limit".to_owned(),
            Value::Number(request.hop_limit.into()),
        ),
        (
            "max_nodes".to_owned(),
            Value::Number((request.max_nodes as u64).into()),
        ),
        (
            "max_edges".to_owned(),
            Value::Number((request.max_edges as u64).into()),
        ),
        (
            "max_hyperedges".to_owned(),
            Value::Number((request.max_hyperedges as u64).into()),
        ),
        (
            "pinned_node_ids".to_owned(),
            Value::Array(
                request
                    .pinned_node_ids
                    .iter()
                    .map(|s| Value::String(s.clone()))
                    .collect(),
            ),
        ),
        (
            "source_watermark".to_owned(),
            request.source_watermark.clone(),
        ),
        (
            "include_tombstones".to_owned(),
            Value::Bool(request.include_tombstones),
        ),
    ])
    .into()
}

#[must_use]
fn _bounded_follow_up_request(
    request: &SemanticLensRequest,
    action: &CockpitAction,
) -> SemanticLensRequest {
    SemanticLensRequest {
        query_text: action.query_text.clone().unwrap_or_default(),
        hop_limit: request.hop_limit.min(2),
        max_nodes: request.max_nodes.min(40),
        max_edges: request.max_edges.min(80),
        max_hyperedges: request.max_hyperedges.min(12),
        explicit_anchor_ids: action.entity_ids.clone(),
        ..request.clone()
    }
}

#[must_use]
fn _inspect_visible_entities(
    snapshot: &SemanticLensSnapshot,
    entity_ids: &[String],
) -> CockpitObservation {
    let nodes: HashMap<&str, &crate::lens_projection::LensNode> =
        snapshot.nodes.iter().map(|n| (n.id.as_str(), n)).collect();
    let edges: HashMap<&str, &crate::lens_projection::LensEdge> = snapshot
        .edges
        .iter()
        .chain(snapshot.hyperedges.iter())
        .map(|e| (e.id.as_str(), e))
        .collect();
    let mut visible: Map<String, Value> = Map::new();
    let mut missing: Vec<String> = Vec::new();
    for entity_id in entity_ids {
        let payload = nodes
            .get(entity_id.as_str())
            .map(|n| n.payload.clone())
            .or_else(|| edges.get(entity_id.as_str()).map(|e| e.payload.clone()));
        if let Some(payload) = payload {
            visible.insert(entity_id.clone(), payload);
        } else {
            missing.push(entity_id.clone());
        }
    }
    let visible_len = visible.len();
    let mut payload = Map::new();
    payload.insert("entities".to_owned(), Value::Object(visible));
    payload.insert(
        "missing_ids".to_owned(),
        Value::Array(missing.iter().map(|id| Value::String(id.clone())).collect()),
    );
    CockpitObservation {
        kind: "inspect_evidence".to_owned(),
        summary: format!(
            "inspected {} visible entities{}",
            visible_len,
            if missing.is_empty() {
                "".to_owned()
            } else {
                format!("; rejected {} out-of-lens ids", missing.len())
            },
        ),
        payload: Value::Object(payload),
    }
}

#[must_use]
fn _evidence_document_ids(
    snapshot: &SemanticLensSnapshot,
    evidence_ids: &HashSet<String>,
) -> HashSet<String> {
    let mut documents: HashSet<String> = HashSet::new();
    for node in &snapshot.nodes {
        if evidence_ids.contains(&node.id) {
            documents.extend(_grounding_document_ids(&node.grounding));
        }
    }
    for edge in snapshot.edges.iter().chain(snapshot.hyperedges.iter()) {
        if evidence_ids.contains(&edge.id) {
            documents.extend(_grounding_document_ids(&edge.grounding));
        }
    }
    documents
}

#[must_use]
fn _grounding_document_ids(grounding: &[Value]) -> HashSet<String> {
    grounding
        .iter()
        .filter_map(|item| {
            item.get("doc_id")
                .or_else(|| item.get("source_document_id"))
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_owned())
        })
        .collect()
}

#[must_use]
fn observation_values(observations: &[CockpitObservation]) -> Vec<Value> {
    observations
        .iter()
        .map(|o| json!({ "kind": o.kind, "summary": o.summary, "payload": o.payload }))
        .collect()
}
