use serde::Serialize;
use serde_json::Value;

use crate::maintenance_status::MaintenanceIntent;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MaintenancePatchValidationIssue {
    pub operation_id: Option<String>,
    pub code: String,
    pub message: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MaintenancePatchValidationReport {
    pub valid: bool,
    pub issues: Vec<MaintenancePatchValidationIssue>,
}

fn issue(
    operation_id: Option<String>,
    code: &str,
    message: &str,
) -> MaintenancePatchValidationIssue {
    MaintenancePatchValidationIssue {
        operation_id,
        code: code.to_owned(),
        message: message.to_owned(),
    }
}

fn created_node_id(operation: &Value) -> Option<String> {
    if operation.get("kind").and_then(Value::as_str) != Some("ADD_NODE") {
        return None;
    }
    operation
        .get("node_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn created_edge_id(operation: &Value) -> Option<String> {
    if operation.get("kind").and_then(Value::as_str) != Some("ADD_EDGE") {
        return None;
    }
    operation
        .get("edge_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn tombstone_target_id(operation: &Value) -> Option<String> {
    let kind = operation.get("kind").and_then(Value::as_str)?;
    match kind {
        "TOMBSTONE_NODE" => operation
            .get("target_id")
            .or_else(|| operation.get("node_id"))
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_owned),
        "TOMBSTONE_EDGE" => operation
            .get("target_id")
            .or_else(|| operation.get("edge_id"))
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_owned),
        _ => None,
    }
}

#[allow(clippy::too_many_lines)]
pub fn validate_maintenance_patch(
    patch: &Value,
    active_node_ids: &[String],
    active_edge_ids: &[String],
    namespace_prefix: Option<&str>,
) -> MaintenancePatchValidationReport {
    let operations = patch
        .get("operations")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let _intent = patch
        .get("intent")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let created_nodes = operations
        .iter()
        .filter_map(created_node_id)
        .collect::<Vec<_>>();
    let created_edges = operations
        .iter()
        .filter_map(created_edge_id)
        .collect::<Vec<_>>();
    let active_or_created_nodes = active_node_ids
        .iter()
        .cloned()
        .chain(created_nodes.iter().cloned())
        .collect::<std::collections::BTreeSet<_>>();
    let active_or_created_ids = active_node_ids
        .iter()
        .cloned()
        .chain(active_edge_ids.iter().cloned())
        .chain(created_nodes.iter().cloned())
        .chain(created_edges.iter().cloned())
        .collect::<std::collections::BTreeSet<_>>();
    let tombstoned_ids = operations
        .iter()
        .filter_map(tombstone_target_id)
        .collect::<std::collections::BTreeSet<_>>();
    let mut issues = Vec::new();
    let mut seen_operation_ids = std::collections::BTreeSet::new();
    let mut seen_created_ids = std::collections::BTreeSet::new();

    for operation in &operations {
        let operation_id = operation
            .get("operation_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        if !seen_operation_ids.insert(operation_id.clone()) {
            issues.push(issue(
                Some(operation_id.clone()),
                "duplicate_operation_id",
                "operation_id must be unique within a patch",
            ));
        }
        let created_id = created_node_id(operation).or_else(|| created_edge_id(operation));
        if let Some(created_id) = created_id {
            if !seen_created_ids.insert(created_id.clone()) {
                issues.push(issue(
                    Some(operation_id.clone()),
                    "duplicate_created_id",
                    "created node or edge ids must be unique within a patch",
                ));
            }
            if namespace_prefix.is_some_and(|prefix| !created_id.starts_with(prefix)) {
                issues.push(issue(
                    Some(operation_id.clone()),
                    "namespace_scope_violation",
                    &format!(
                        "created id must start with namespace prefix {prefix:?}",
                        prefix = namespace_prefix.unwrap_or_default()
                    ),
                ));
            }
        }
        let kind = operation
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if operation.get("provenance").is_none()
            && ["ADD_NODE", "ADD_EDGE", "TOMBSTONE_NODE", "TOMBSTONE_EDGE"].contains(&kind)
        {
            issues.push(issue(
                Some(operation_id.clone()),
                "missing_provenance",
                "graph-changing operations require provenance",
            ));
        }
        if kind == "ADD_EDGE" {
            for field in ["from_node_id", "to_node_id"] {
                let endpoint = operation
                    .get(field)
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if !active_or_created_nodes.contains(endpoint) {
                    issues.push(issue(
                        Some(operation_id.clone()),
                        "missing_edge_endpoint",
                        &format!("ADD_EDGE {field} must reference an active or same-patch node"),
                    ));
                }
            }
        }
        if kind == "TOMBSTONE_NODE" {
            let target = tombstone_target_id(operation).unwrap_or_default();
            if !active_node_ids.contains(&target) {
                issues.push(issue(
                    Some(operation_id.clone()),
                    "missing_tombstone_target",
                    "TOMBSTONE_NODE target must reference an active node",
                ));
            }
        }
        if kind == "TOMBSTONE_EDGE" {
            let target = tombstone_target_id(operation).unwrap_or_default();
            if !active_edge_ids.contains(&target) {
                issues.push(issue(
                    Some(operation_id.clone()),
                    "missing_tombstone_target",
                    "TOMBSTONE_EDGE target must reference an active edge",
                ));
            }
        }
        let supersedes_ids = operation
            .get("supersedes_ids")
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        if !supersedes_ids.is_empty() && !matches!(kind, "ADD_NODE" | "ADD_EDGE") {
            issues.push(issue(
                Some(operation_id.clone()),
                "invalid_supersession",
                "supersedes_ids must be declared on replacement ADD_NODE or ADD_EDGE operations",
            ));
        }
        for superseded_id in supersedes_ids {
            if !active_or_created_ids.contains(&superseded_id) {
                issues.push(issue(
                    Some(operation_id.clone()),
                    "missing_supersession_target",
                    "supersedes_ids must reference an active or same-patch graph object",
                ));
            }
            if !tombstoned_ids.contains(&superseded_id) {
                issues.push(issue(
                    Some(operation_id.clone()),
                    "unpaired_supersession",
                    "replacement operations must be paired with a tombstone for each superseded id",
                ));
            }
        }
    }
    MaintenancePatchValidationReport {
        valid: issues.is_empty(),
        issues,
    }
}

#[must_use]
pub fn intent_is_crosslink(intent: MaintenanceIntent) -> bool {
    matches!(
        intent,
        MaintenanceIntent::DeriveCrosslinkCandidate
            | MaintenanceIntent::AddCrosslink
            | MaintenanceIntent::RetractCrosslink
    )
}
