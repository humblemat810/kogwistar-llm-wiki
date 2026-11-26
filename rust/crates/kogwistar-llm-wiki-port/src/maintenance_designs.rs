use serde::Serialize;
use serde_json::{json, Value};

use crate::ids::stable_id;
use crate::maintenance_policy::{
    CROSSLINK_GROUP_WORKFLOW_ID, DERIVED_KNOWLEDGE_WORKFLOW_ID, EXECUTION_WISDOM_WORKFLOW_ID,
    GRAPH_PATCH_APPLY_WORKFLOW_ID, GRAPH_PATCH_PROPOSAL_WORKFLOW_ID,
    MULTIMODAL_RETRIEVAL_WORKFLOW_ID,
};

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct WorkflowNode {
    pub id: String,
    pub label: String,
    pub kind: String,
    pub summary: String,
    pub metadata: Value,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct WorkflowEdge {
    pub id: String,
    pub source_ids: Vec<String>,
    pub target_ids: Vec<String>,
    pub relation: String,
    pub kind: String,
    pub label: String,
    pub summary: String,
    pub metadata: Value,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct WorkflowDesignArtifact {
    pub workflow_id: String,
    pub workflow_version: String,
    pub start_node_id: String,
    pub nodes: Vec<WorkflowNode>,
    pub edges: Vec<WorkflowEdge>,
}

#[allow(dead_code)]
fn grounding() -> Value {
    json!([{
        "spans": [{
            "doc_id": "dummy",
            "start_char": 0,
            "end_char": 1,
            "excerpt": "",
            "document_page_url": "",
            "collection_page_url": "",
            "insertion_method": "",
            "page_number": 1,
            "context_before": "",
            "context_after": "",
            "chunk_id": null,
            "source_cluster_id": null,
            "verification": null
        }]
    }])
}

fn workflow_node(
    id: &str,
    label: &str,
    kind: &str,
    summary: &str,
    metadata: Value,
) -> WorkflowNode {
    WorkflowNode {
        id: id.to_owned(),
        label: label.to_owned(),
        kind: kind.to_owned(),
        summary: summary.to_owned(),
        metadata,
    }
}

fn terminal_node(workflow_id: &str, node_id: &str, label: &str, summary: &str) -> WorkflowNode {
    workflow_node(
        node_id,
        label,
        "entity",
        summary,
        json!({
            "entity_type": "workflow_node",
            "workflow_id": workflow_id,
            "wf_terminal": true
        }),
    )
}

fn workflow_edge(
    workflow_id: &str,
    edge_key: &str,
    source_id: &str,
    target_id: &str,
    label: &str,
    summary: &str,
) -> WorkflowEdge {
    WorkflowEdge {
        id: stable_id("wf_edge", &[workflow_id, edge_key]).to_string(),
        source_ids: vec![source_id.to_owned()],
        target_ids: vec![target_id.to_owned()],
        relation: "workflow_transition".to_owned(),
        kind: "relationship".to_owned(),
        label: label.to_owned(),
        summary: summary.to_owned(),
        metadata: json!({
            "entity_type": "workflow_edge",
            "workflow_id": workflow_id,
            "wf_predicate": null,
            "wf_is_default": true
        }),
    }
}

#[must_use]
pub fn build_derived_knowledge_design(workflow_id: Option<&str>) -> WorkflowDesignArtifact {
    let workflow_id = workflow_id.unwrap_or(DERIVED_KNOWLEDGE_WORKFLOW_ID);
    let distill = stable_id("wf_node", &[workflow_id, "distill"]).to_string();
    let check_done = stable_id("wf_node", &[workflow_id, "check_done"]).to_string();
    let done = stable_id("wf_node", &[workflow_id, "done"]).to_string();
    let nodes = vec![
        workflow_node(
            distill.as_str(),
            "Derive Knowledge Synthesis",
            "entity",
            "Aggregate promoted knowledge into derived-knowledge artifacts.",
            json!({
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "distill",
                "wf_start": true,
                "default_context_window": 4000
            }),
        ),
        workflow_node(
            check_done.as_str(),
            "Check Derived Knowledge Complete",
            "entity",
            "Confirm derived-knowledge synthesis is ready to finalize.",
            json!({
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "check_done",
                "default_context_window": 4000
            }),
        ),
        terminal_node(
            workflow_id,
            done.as_str(),
            "Derived Knowledge Complete",
            "Terminal state for derived-knowledge synthesis.",
        ),
    ];
    let edges = vec![
        workflow_edge(
            workflow_id,
            "distill_to_done",
            distill.as_str(),
            check_done.as_str(),
            "check",
            "Derived-knowledge synthesis ready for completion check.",
        ),
        workflow_edge(
            workflow_id,
            "check_to_done",
            check_done.as_str(),
            done.as_str(),
            "finished",
            "Derived-knowledge synthesis complete.",
        ),
    ];
    WorkflowDesignArtifact {
        workflow_id: workflow_id.to_owned(),
        workflow_version: "v1".to_owned(),
        start_node_id: distill,
        nodes,
        edges,
    }
}

#[must_use]
pub fn build_execution_wisdom_design(workflow_id: Option<&str>) -> WorkflowDesignArtifact {
    let workflow_id = workflow_id.unwrap_or(EXECUTION_WISDOM_WORKFLOW_ID);
    let extract = stable_id(
        "wf_node",
        &[workflow_id, "derive_problem_solving_wisdom_from_history"],
    )
    .to_string();
    let done = stable_id("wf_node", &[workflow_id, "done"]).to_string();
    let nodes = vec![
        workflow_node(
            extract.as_str(),
            "Derive Problem-Solving Wisdom From History",
            "entity",
            "Read workflow failure history and emit execution-wisdom artifacts.",
            json!({
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "derive_problem_solving_wisdom_from_history",
                "wf_start": true,
                "default_context_window": 4000
            }),
        ),
        terminal_node(
            workflow_id,
            done.as_str(),
            "Execution Wisdom Complete",
            "Terminal state for execution-history wisdom extraction.",
        ),
    ];
    let edges = vec![workflow_edge(
        workflow_id,
        "extract_to_done",
        extract.as_str(),
        done.as_str(),
        "finished",
        "Execution-wisdom extraction complete.",
    )];
    WorkflowDesignArtifact {
        workflow_id: workflow_id.to_owned(),
        workflow_version: "v1".to_owned(),
        start_node_id: extract,
        nodes,
        edges,
    }
}

#[must_use]
pub fn build_graph_patch_design(
    workflow_id: &str,
    label: &str,
    summary: &str,
) -> WorkflowDesignArtifact {
    let node_noop = stable_id("wf_node", &[workflow_id, "noop"]).to_string();
    let terminal = stable_id("wf_node", &[workflow_id, "done"]).to_string();
    let nodes = vec![
        workflow_node(
            node_noop.as_str(),
            label,
            "entity",
            summary,
            json!({
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "noop",
                "wf_start": true,
                "default_context_window": 4000
            }),
        ),
        terminal_node(
            workflow_id,
            terminal.as_str(),
            &format!("{label} Complete"),
            &format!("Terminal state for {}", summary.to_ascii_lowercase()),
        ),
    ];
    let edges = vec![workflow_edge(
        workflow_id,
        "noop_to_done",
        node_noop.as_str(),
        terminal.as_str(),
        "finished",
        "Graph patch workflow completed.",
    )];
    WorkflowDesignArtifact {
        workflow_id: workflow_id.to_owned(),
        workflow_version: "v1".to_owned(),
        start_node_id: node_noop,
        nodes,
        edges,
    }
}

#[must_use]
pub fn build_graph_patch_proposal_design(workflow_id: Option<&str>) -> WorkflowDesignArtifact {
    build_graph_patch_design(
        workflow_id.unwrap_or(GRAPH_PATCH_PROPOSAL_WORKFLOW_ID),
        "Graph Patch Proposal",
        "Validate and prepare graph-patch proposal jobs.",
    )
}

#[must_use]
pub fn build_graph_patch_apply_design(workflow_id: Option<&str>) -> WorkflowDesignArtifact {
    build_graph_patch_design(
        workflow_id.unwrap_or(GRAPH_PATCH_APPLY_WORKFLOW_ID),
        "Graph Patch Apply",
        "Apply validated graph-patch jobs.",
    )
}

#[must_use]
pub fn build_crosslink_group_design(workflow_id: Option<&str>) -> WorkflowDesignArtifact {
    let workflow_id = workflow_id.unwrap_or(CROSSLINK_GROUP_WORKFLOW_ID);
    let stages = [
        (
            "select",
            "Select Bounded Candidates",
            "Select active, authorized workspace concepts.",
        ),
        (
            "evidence",
            "Assemble Source Evidence",
            "Resolve immutable revisions and exact source spans.",
        ),
        (
            "propose",
            "Propose Cross-Link Groups",
            "Generate bounded groups using supplied evidence IDs only.",
        ),
        (
            "validate",
            "Validate Groups",
            "Check endpoints, scope, ACL, ParseView, and grounding.",
        ),
        (
            "critic",
            "Critic Review Per Group",
            "Review each group independently; store verdict and citations.",
        ),
        (
            "route",
            "Route By Approval Policy",
            "Require critic approval for automatic application or human review.",
        ),
        (
            "pending",
            "Persist Pending Review",
            "Persist a workspace-scoped evaluation artifact and continue exploration.",
        ),
        (
            "apply",
            "Apply Approved Group",
            "Apply each accepted group as its own patch.",
        ),
        (
            "outcome",
            "Record Group Outcome",
            "Record applied, rejected, partial, stale, or review-required outcome.",
        ),
        (
            "continue",
            "Continue Exploration",
            "Continue bounded maintenance independently of pending decisions.",
        ),
    ];
    let nodes = stages
        .iter()
        .enumerate()
        .map(|(index, (key, label, summary))| {
            let mut metadata = json!({
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": format!("crosslink_{key}"),
                "default_context_window": 4000
            });
            if index == 0 {
                metadata["wf_start"] = json!(true);
            }
            if *key == "continue" {
                metadata["wf_terminal"] = json!(true);
            }
            workflow_node(
                stable_id("wf_node", &[workflow_id, key])
                    .to_string()
                    .as_str(),
                label,
                "entity",
                summary,
                metadata,
            )
        })
        .collect::<Vec<_>>();
    let ids = stages
        .iter()
        .map(|(key, _, _)| {
            (
                key.to_owned(),
                stable_id("wf_node", &[workflow_id, key]).to_string(),
            )
        })
        .collect::<std::collections::BTreeMap<_, _>>();
    let transitions = [
        ("select", "evidence", "selected"),
        ("evidence", "propose", "evidence_ready"),
        ("propose", "validate", "groups_proposed"),
        ("validate", "critic", "groups_validated"),
        ("critic", "route", "groups_reviewed"),
        ("route", "pending", "human_review_required"),
        ("route", "apply", "automatic_approval"),
        ("pending", "outcome", "human_review_completed"),
        ("apply", "outcome", "group_application_recorded"),
        ("outcome", "continue", "outcome_recorded"),
    ];
    let edges = transitions
        .iter()
        .map(|(source, target, label)| {
            workflow_edge(
                workflow_id,
                &format!("{source}_to_{target}"),
                ids[source].as_str(),
                ids[target].as_str(),
                label,
                "Cross-link group workflow transition.",
            )
        })
        .collect::<Vec<_>>();
    WorkflowDesignArtifact {
        workflow_id: workflow_id.to_owned(),
        workflow_version: "v1".to_owned(),
        start_node_id: ids["select"].clone(),
        nodes,
        edges,
    }
}

#[must_use]
pub fn build_multimodal_retrieval_design(workflow_id: Option<&str>) -> WorkflowDesignArtifact {
    let workflow_id = workflow_id.unwrap_or(MULTIMODAL_RETRIEVAL_WORKFLOW_ID);
    let dispatch = stable_id("wf_node", &[workflow_id, "multimodal_dispatch"]).to_string();
    let sidecar = stable_id("wf_node", &[workflow_id, "multimodal_sidecar"]).to_string();
    let authorize = stable_id("wf_node", &[workflow_id, "multimodal_authorize"]).to_string();
    let nodes = vec![
        workflow_node(
            dispatch.as_str(),
            "Dispatch Multimodal Retrieval",
            "entity",
            "Resolve bounded multimodal retrieval requests.",
            json!({
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "multimodal_dispatch",
                "wf_start": true,
                "default_context_window": 4000
            }),
        ),
        workflow_node(
            sidecar.as_str(),
            "Query Multimodal Sidecar",
            "entity",
            "Query the isolated sidecar representation store.",
            json!({
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "multimodal_sidecar",
                "default_context_window": 4000
            }),
        ),
        terminal_node(
            workflow_id,
            authorize.as_str(),
            "Multimodal Retrieval Complete",
            "Terminal state after ACL-safe multimodal retrieval.",
        ),
    ];
    let edges = vec![
        workflow_edge(
            workflow_id,
            "dispatch_to_sidecar",
            dispatch.as_str(),
            sidecar.as_str(),
            "dispatch",
            "Multimodal request admitted to sidecar retrieval.",
        ),
        workflow_edge(
            workflow_id,
            "sidecar_to_authorize",
            sidecar.as_str(),
            authorize.as_str(),
            "authorize",
            "Multimodal retrieval complete.",
        ),
    ];
    WorkflowDesignArtifact {
        workflow_id: workflow_id.to_owned(),
        workflow_version: "v1".to_owned(),
        start_node_id: dispatch,
        nodes,
        edges,
    }
}
