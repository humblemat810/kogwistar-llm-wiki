use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;

use crate::maintenance_status::MaintenanceIntent;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum MaintenanceOperationKind {
    AddNode,
    AddEdge,
    TombstoneNode,
    TombstoneEdge,
    RequestReview,
    Noop,
}

impl MaintenanceOperationKind {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AddNode => "ADD_NODE",
            Self::AddEdge => "ADD_EDGE",
            Self::TombstoneNode => "TOMBSTONE_NODE",
            Self::TombstoneEdge => "TOMBSTONE_EDGE",
            Self::RequestReview => "REQUEST_REVIEW",
            Self::Noop => "NOOP",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MaintenanceScopeKind {
    Workspace,
    Conversation,
    Thread,
}

impl MaintenanceScopeKind {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Workspace => "workspace",
            Self::Conversation => "conversation",
            Self::Thread => "thread",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenanceScope {
    pub workspace_id: String,
    pub scope_kind: String,
    pub conversation_id: Option<String>,
    pub thread_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenanceProvenance {
    pub source_document_id: Option<String>,
    pub source_span_ids: Vec<String>,
    pub source_pointers: Vec<Value>,
    pub maintenance_run_id: String,
    pub confidence: f64,
}

#[must_use]
pub fn validate_provenance_grounding(provenance: &MaintenanceProvenance) -> bool {
    provenance.source_document_id.is_some()
        || !provenance.source_span_ids.is_empty()
        || !provenance.source_pointers.is_empty()
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenancePatchOperation {
    pub operation_id: String,
    pub kind: MaintenanceOperationKind,
    pub target_id: Option<String>,
    pub node_id: Option<String>,
    pub edge_id: Option<String>,
    pub from_node_id: Option<String>,
    pub to_node_id: Option<String>,
    pub relation: Option<String>,
    pub label: Option<String>,
    pub node_type: Option<String>,
    pub properties: Value,
    pub supersedes_ids: Vec<String>,
    pub provenance: Option<MaintenanceProvenance>,
    pub reason: Option<String>,
}

impl MaintenancePatchOperation {
    #[must_use]
    pub fn shape_errors(&self) -> Vec<String> {
        let mut errors = Vec::new();
        match self.kind {
            MaintenanceOperationKind::AddNode => {
                if self.node_id.is_none() {
                    errors.push("ADD_NODE requires node_id".to_owned());
                }
            }
            MaintenanceOperationKind::AddEdge => {
                if self.edge_id.is_none() {
                    errors.push("ADD_EDGE requires edge_id".to_owned());
                }
                if self.from_node_id.is_none() {
                    errors.push("ADD_EDGE requires from_node_id".to_owned());
                }
                if self.to_node_id.is_none() {
                    errors.push("ADD_EDGE requires to_node_id".to_owned());
                }
                if self.relation.is_none() {
                    errors.push("ADD_EDGE requires relation".to_owned());
                }
            }
            MaintenanceOperationKind::TombstoneNode => {
                if self.target_id.is_none() && self.node_id.is_none() {
                    errors.push("TOMBSTONE_NODE requires target_id or node_id".to_owned());
                }
            }
            MaintenanceOperationKind::TombstoneEdge => {
                if self.target_id.is_none() && self.edge_id.is_none() {
                    errors.push("TOMBSTONE_EDGE requires target_id or edge_id".to_owned());
                }
            }
            MaintenanceOperationKind::RequestReview => {
                if self.reason.is_none() {
                    errors.push("REQUEST_REVIEW requires reason".to_owned());
                }
            }
            MaintenanceOperationKind::Noop => {}
        }
        errors
    }

    #[must_use]
    pub fn created_node_id(&self) -> Option<&str> {
        if self.kind == MaintenanceOperationKind::AddNode {
            self.node_id.as_deref()
        } else {
            None
        }
    }

    #[must_use]
    pub fn created_edge_id(&self) -> Option<&str> {
        if self.kind == MaintenanceOperationKind::AddEdge {
            self.edge_id.as_deref()
        } else {
            None
        }
    }

    #[must_use]
    pub fn tombstone_target_id(&self) -> Option<&str> {
        match self.kind {
            MaintenanceOperationKind::TombstoneNode => {
                self.target_id.as_deref().or(self.node_id.as_deref())
            }
            MaintenanceOperationKind::TombstoneEdge => {
                self.target_id.as_deref().or(self.edge_id.as_deref())
            }
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenancePatch {
    pub patch_id: String,
    pub intent: MaintenanceIntent,
    pub scope: MaintenanceScope,
    pub operations: Vec<MaintenancePatchOperation>,
    pub status: String,
    pub rationale: Option<String>,
    pub requires_atomic_group: bool,
}

impl MaintenancePatch {
    #[must_use]
    pub fn requires_atomic_replacement(&self) -> bool {
        self.requires_atomic_group
            || self
                .operations
                .iter()
                .any(|op| !op.supersedes_ids.is_empty())
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenancePatchValidationIssue {
    pub operation_id: Option<String>,
    pub code: String,
    pub message: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenancePatchValidationReport {
    pub valid: bool,
    pub issues: Vec<MaintenancePatchValidationIssue>,
}

#[must_use]
pub fn validate_maintenance_patch(
    patch: &MaintenancePatch,
    active_node_ids: &[String],
    active_edge_ids: &[String],
) -> MaintenancePatchValidationReport {
    let active_node_ids: HashSet<&str> = active_node_ids.iter().map(String::as_str).collect();
    let active_edge_ids: HashSet<&str> = active_edge_ids.iter().map(String::as_str).collect();
    let created_nodes: HashSet<&str> = patch
        .operations
        .iter()
        .filter_map(|op| op.created_node_id())
        .collect();
    let created_edges: HashSet<&str> = patch
        .operations
        .iter()
        .filter_map(|op| op.created_edge_id())
        .collect();
    let active_or_created_nodes: HashSet<&str> =
        active_node_ids.union(&created_nodes).cloned().collect();
    let mut active_or_created_ids: HashSet<&str> =
        active_node_ids.union(&active_edge_ids).cloned().collect();
    active_or_created_ids.extend(created_nodes.iter().copied());
    active_or_created_ids.extend(created_edges.iter().copied());

    let mut issues: Vec<MaintenancePatchValidationIssue> = Vec::new();
    let mut seen_operation_ids: HashSet<&str> = HashSet::new();
    let mut seen_created_ids: HashSet<&str> = HashSet::new();

    for operation in &patch.operations {
        if !seen_operation_ids.insert(operation.operation_id.as_str()) {
            issues.push(MaintenancePatchValidationIssue {
                operation_id: Some(operation.operation_id.clone()),
                code: "duplicate_operation_id".to_owned(),
                message: "operation_id must be unique within a patch".to_owned(),
            });
        }

        for error in operation.shape_errors() {
            issues.push(MaintenancePatchValidationIssue {
                operation_id: Some(operation.operation_id.clone()),
                code: "invalid_operation_shape".to_owned(),
                message: error,
            });
        }

        if operation
            .created_node_id()
            .or(operation.created_edge_id())
            .filter(|created_id| !seen_created_ids.insert(created_id))
            .is_some()
        {
            issues.push(MaintenancePatchValidationIssue {
                operation_id: Some(operation.operation_id.clone()),
                code: "duplicate_created_id".to_owned(),
                message: "created node or edge ids must be unique within a patch".to_owned(),
            });
        }

        if matches!(
            operation.kind,
            MaintenanceOperationKind::AddNode
                | MaintenanceOperationKind::AddEdge
                | MaintenanceOperationKind::TombstoneNode
                | MaintenanceOperationKind::TombstoneEdge
        ) && operation.provenance.is_none()
        {
            issues.push(MaintenancePatchValidationIssue {
                operation_id: Some(operation.operation_id.clone()),
                code: "missing_provenance".to_owned(),
                message: "graph-changing operations require provenance".to_owned(),
            });
        }

        if operation.kind == MaintenanceOperationKind::AddEdge {
            for value in [
                operation.from_node_id.as_deref(),
                operation.to_node_id.as_deref(),
            ] {
                if value
                    .filter(|node_id| !active_or_created_nodes.contains(node_id))
                    .is_some()
                {
                    issues.push(MaintenancePatchValidationIssue {
                        operation_id: Some(operation.operation_id.clone()),
                        code: "missing_edge_endpoint".to_owned(),
                        message: "ADD_EDGE endpoint must reference an active or same-patch node"
                            .to_owned(),
                    });
                }
            }
        }
    }

    let valid = issues.is_empty();
    MaintenancePatchValidationReport { valid, issues }
}
