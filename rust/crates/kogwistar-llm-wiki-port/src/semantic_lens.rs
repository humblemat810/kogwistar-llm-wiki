//! Semantic-lens snapshots and request envelopes: deterministic, bounded,
//! JSON-serializable presentation data. Mirrors
//! `kogwistar_llm_wiki.workbench.semantic_lens`; this module only serializes
//! caller-supplied lens data and never becomes graph truth.

use serde_json::{Map, Value};

use crate::lens_projection::{
    LensEdge, LensNode, LensParticipation, RetrievalStatus, SelectionExplanation,
    SemanticLensRequest, SemanticLensSnapshot,
};

impl SemanticLensSnapshot {
    /// Mirror `SemanticLensSnapshot.to_dict` / `_jsonable(asdict(self))`.
    #[must_use]
    pub fn to_dict(&self) -> Value {
        Value::Object(Map::from_iter([
            ("lens_id".to_owned(), Value::String(self.lens_id.clone())),
            (
                "workspace_id".to_owned(),
                Value::String(self.workspace_id.clone()),
            ),
            ("source_watermark".to_owned(), self.source_watermark.clone()),
            (
                "projected_at_ms".to_owned(),
                Value::Number(self.projected_at_ms.into()),
            ),
            (
                "completeness".to_owned(),
                Value::String(self.completeness.clone()),
            ),
            (
                "nodes".to_owned(),
                Value::Array(self.nodes.iter().map(lens_node_value).collect()),
            ),
            (
                "edges".to_owned(),
                Value::Array(self.edges.iter().map(lens_edge_value).collect()),
            ),
            (
                "hyperedges".to_owned(),
                Value::Array(self.hyperedges.iter().map(lens_edge_value).collect()),
            ),
            (
                "participations".to_owned(),
                Value::Array(
                    self.participations
                        .iter()
                        .map(lens_participation_value)
                        .collect(),
                ),
            ),
            (
                "anchor_explanations".to_owned(),
                Value::Array(
                    self.anchor_explanations
                        .iter()
                        .map(selection_explanation_value)
                        .collect(),
                ),
            ),
            (
                "selection_explanations".to_owned(),
                Value::Array(
                    self.selection_explanations
                        .iter()
                        .map(selection_explanation_value)
                        .collect(),
                ),
            ),
            ("omitted_summary".to_owned(), self.omitted_summary.clone()),
            (
                "query_timing_ms".to_owned(),
                Value::Number(self.query_timing_ms.into()),
            ),
            (
                "retrieval".to_owned(),
                retrieval_status_value(&self.retrieval),
            ),
            (
                "flat_hits".to_owned(),
                Value::Array(self.flat_hits.to_vec()),
            ),
        ]))
    }
}

#[must_use]
fn lens_node_value(node: &LensNode) -> Value {
    Value::Object(Map::from_iter([
        ("id".to_owned(), Value::String(node.id.clone())),
        (
            "graph_space".to_owned(),
            Value::String(node.graph_space.clone()),
        ),
        (
            "namespace".to_owned(),
            Value::String(node.namespace.clone()),
        ),
        ("label".to_owned(), Value::String(node.label.clone())),
        (
            "node_type".to_owned(),
            Value::String(node.node_type.clone()),
        ),
        (
            "entity_revision".to_owned(),
            node.entity_revision.clone().unwrap_or(Value::Null),
        ),
        ("metadata".to_owned(), node.metadata.clone()),
        (
            "grounding".to_owned(),
            Value::Array(node.grounding.to_vec()),
        ),
        ("payload".to_owned(), node.payload.clone()),
    ]))
}

#[must_use]
fn lens_edge_value(edge: &LensEdge) -> Value {
    Value::Object(Map::from_iter([
        ("id".to_owned(), Value::String(edge.id.clone())),
        (
            "graph_space".to_owned(),
            Value::String(edge.graph_space.clone()),
        ),
        (
            "namespace".to_owned(),
            Value::String(edge.namespace.clone()),
        ),
        (
            "source_ids".to_owned(),
            Value::Array(
                edge.source_ids
                    .iter()
                    .map(|s| Value::String(s.clone()))
                    .collect::<Vec<_>>(),
            ),
        ),
        (
            "target_ids".to_owned(),
            Value::Array(
                edge.target_ids
                    .iter()
                    .map(|s| Value::String(s.clone()))
                    .collect::<Vec<_>>(),
            ),
        ),
        ("relation".to_owned(), Value::String(edge.relation.clone())),
        (
            "entity_revision".to_owned(),
            edge.entity_revision.clone().unwrap_or(Value::Null),
        ),
        ("metadata".to_owned(), edge.metadata.clone()),
        (
            "grounding".to_owned(),
            Value::Array(edge.grounding.to_vec()),
        ),
        ("payload".to_owned(), edge.payload.clone()),
    ]))
}

#[must_use]
fn lens_participation_value(p: &LensParticipation) -> Value {
    Value::Object(Map::from_iter([
        ("edge_id".to_owned(), Value::String(p.edge_id.clone())),
        ("node_id".to_owned(), Value::String(p.node_id.clone())),
        ("role".to_owned(), Value::String(p.role.clone())),
    ]))
}

#[must_use]
fn selection_explanation_value(e: &SelectionExplanation) -> Value {
    Value::Object(Map::from_iter([
        ("node_id".to_owned(), Value::String(e.node_id.clone())),
        ("reason".to_owned(), Value::String(e.reason.clone())),
        ("score".to_owned(), json_number(e.score)),
        (
            "matched_terms".to_owned(),
            Value::Array(
                e.matched_terms
                    .iter()
                    .map(|s| Value::String(s.clone()))
                    .collect::<Vec<_>>(),
            ),
        ),
        (
            "distance".to_owned(),
            e.distance.map_or(Value::Null, |d| Value::Number(d.into())),
        ),
    ]))
}

#[must_use]
fn retrieval_status_value(s: &RetrievalStatus) -> Value {
    Value::Object(Map::from_iter([
        ("mode".to_owned(), Value::String(s.mode.clone())),
        (
            "semantic_available".to_owned(),
            Value::Bool(s.semantic_available),
        ),
        (
            "backend".to_owned(),
            s.backend.clone().map_or(Value::Null, Value::String),
        ),
        (
            "metric".to_owned(),
            s.metric.clone().map_or(Value::Null, Value::String),
        ),
        (
            "degraded_reason".to_owned(),
            s.degraded_reason.clone().map_or(Value::Null, Value::String),
        ),
        ("score_kind".to_owned(), Value::String(s.score_kind.clone())),
    ]))
}

#[must_use]
fn json_number(f: f64) -> Value {
    Value::Number(serde_json::Number::from_f64(f).unwrap_or(0.into()))
}

impl SemanticLensRequest {
    /// Mirror `_request_payload(request)`: the stable wire envelope of a lens
    /// request, used by proposal envelopes and cockpit final_request.
    #[must_use]
    pub fn to_dict(&self) -> Value {
        Value::Object(Map::from_iter([
            (
                "workspace_id".to_owned(),
                Value::String(self.workspace_id.clone()),
            ),
            (
                "graph_spaces".to_owned(),
                Value::Array(
                    self.graph_spaces
                        .iter()
                        .map(|s| Value::String(s.clone()))
                        .collect::<Vec<_>>(),
                ),
            ),
            (
                "query_text".to_owned(),
                Value::String(self.query_text.clone()),
            ),
            (
                "semantic_retrieval".to_owned(),
                Value::Bool(self.semantic_retrieval),
            ),
            (
                "retrieval_mode".to_owned(),
                Value::String(self.retrieval_mode.as_str().to_owned()),
            ),
            (
                "retrieval_required".to_owned(),
                Value::Bool(self.retrieval_required),
            ),
            (
                "similarity_threshold".to_owned(),
                self.similarity_threshold.map_or(Value::Null, json_number),
            ),
            (
                "source_evidence_required".to_owned(),
                Value::Bool(self.source_evidence_required),
            ),
            (
                "explicit_anchor_ids".to_owned(),
                Value::Array(
                    self.explicit_anchor_ids
                        .iter()
                        .map(|s| Value::String(s.clone()))
                        .collect::<Vec<_>>(),
                ),
            ),
            ("hop_limit".to_owned(), Value::Number(self.hop_limit.into())),
            (
                "max_nodes".to_owned(),
                Value::Number((self.max_nodes as u64).into()),
            ),
            (
                "max_edges".to_owned(),
                Value::Number((self.max_edges as u64).into()),
            ),
            (
                "max_hyperedges".to_owned(),
                Value::Number((self.max_hyperedges as u64).into()),
            ),
            (
                "pinned_node_ids".to_owned(),
                Value::Array(
                    self.pinned_node_ids
                        .iter()
                        .map(|s| Value::String(s.clone()))
                        .collect::<Vec<_>>(),
                ),
            ),
            ("source_watermark".to_owned(), self.source_watermark.clone()),
            (
                "include_tombstones".to_owned(),
                Value::Bool(self.include_tombstones),
            ),
        ]))
    }
}
