use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MaintenanceCandidate {
    pub candidate_id: String,
    pub reason: String,
    pub score: Option<f64>,
    pub metadata: Option<Value>,
}

impl MaintenanceCandidate {
    #[must_use]
    pub fn as_json(&self) -> Value {
        json!({
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "score": self.score,
            "metadata": self.metadata
        })
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SelectionNode {
    pub id: String,
    #[serde(default)]
    pub embedding: Option<Vec<f64>>,
    #[serde(default)]
    pub metadata: Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SelectionEdge {
    #[serde(default)]
    pub source_ids: Vec<String>,
    #[serde(default)]
    pub target_ids: Vec<String>,
}

fn vector(node: &SelectionNode) -> Option<Vec<f64>> {
    let raw = node.embedding.clone().or_else(|| {
        node.metadata
            .as_object()
            .and_then(|values| values.get("embedding"))
            .and_then(Value::as_array)
            .map(|values| values.iter().filter_map(Value::as_f64).collect::<Vec<_>>())
    })?;
    if raw.is_empty() || raw.iter().any(|value| !value.is_finite()) {
        return None;
    }
    Some(raw)
}

fn cosine(left: &[f64], right: &[f64]) -> Option<f64> {
    if left.len() != right.len() {
        return None;
    }
    let dot = left.iter().zip(right).map(|(a, b)| a * b).sum::<f64>();
    let left_norm = left.iter().map(|value| value * value).sum::<f64>().sqrt();
    let right_norm = right.iter().map(|value| value * value).sum::<f64>().sqrt();
    let denominator = left_norm * right_norm;
    if denominator == 0.0 {
        None
    } else {
        Some(dot / denominator)
    }
}

fn edge_endpoints(edge: &SelectionEdge) -> BTreeSet<String> {
    edge.source_ids
        .iter()
        .chain(edge.target_ids.iter())
        .filter(|value| !value.is_empty())
        .cloned()
        .collect()
}

#[must_use]
pub fn select_request_candidates(
    seed_nodes: &[SelectionNode],
    candidate_nodes: &[SelectionNode],
    edges: &[SelectionEdge],
    max_candidates: usize,
    semantic_min_score: f64,
) -> Vec<MaintenanceCandidate> {
    let seeds = seed_nodes
        .iter()
        .map(|node| node.id.clone())
        .filter(|id| !id.is_empty())
        .collect::<BTreeSet<_>>();
    let mut connected = BTreeSet::new();
    for edge in edges {
        let endpoints = edge_endpoints(edge);
        if endpoints.iter().any(|endpoint| seeds.contains(endpoint)) {
            connected.extend(
                endpoints
                    .iter()
                    .filter(|endpoint| !seeds.contains(*endpoint))
                    .cloned(),
            );
        }
    }
    let by_id = candidate_nodes
        .iter()
        .filter(|node| !node.id.is_empty())
        .map(|node| (node.id.clone(), node))
        .collect::<BTreeMap<_, _>>();
    let mut output = connected
        .iter()
        .filter(|node_id| by_id.contains_key(*node_id))
        .map(|node_id| MaintenanceCandidate {
            candidate_id: node_id.clone(),
            reason: "connected_neighbor".to_owned(),
            score: None,
            metadata: Some(json!({})),
        })
        .collect::<Vec<_>>();
    let mut selected = output
        .iter()
        .map(|item| item.candidate_id.clone())
        .collect::<BTreeSet<_>>();
    selected.extend(seeds.iter().cloned());

    let seed_vectors = seed_nodes.iter().filter_map(vector).collect::<Vec<_>>();
    if !seed_vectors.is_empty() {
        let mut semantic = Vec::new();
        for (node_id, node) in &by_id {
            if selected.contains(node_id) {
                continue;
            }
            let Some(node_vector) = vector(node) else {
                continue;
            };
            let score = seed_vectors
                .iter()
                .filter_map(|seed_vector| cosine(seed_vector, &node_vector))
                .fold(None::<f64>, |best, value| match best {
                    Some(current) if current >= value => Some(current),
                    _ => Some(value),
                });
            if score.is_some_and(|value| value >= semantic_min_score) {
                semantic.push(MaintenanceCandidate {
                    candidate_id: node_id.clone(),
                    reason: "semantic_similar".to_owned(),
                    score,
                    metadata: Some(json!({})),
                });
            }
        }
        semantic.sort_by(|left, right| {
            right
                .score
                .partial_cmp(&left.score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(left.candidate_id.cmp(&right.candidate_id))
        });
        output.extend(semantic);
    }

    let mut selected = output
        .iter()
        .map(|item| item.candidate_id.clone())
        .collect::<BTreeSet<_>>();
    selected.extend(seeds.iter().cloned());
    for (node_id, node) in by_id {
        if selected.contains(&node_id) {
            continue;
        }
        let metadata = node.metadata.as_object();
        let Some(metadata) = metadata else {
            continue;
        };
        if [
            "source_ids",
            "evidence_ids",
            "history_ids",
            "related_node_ids",
        ]
        .iter()
        .any(|key| metadata.contains_key(*key))
        {
            output.push(MaintenanceCandidate {
                candidate_id: node_id,
                reason: if metadata.contains_key("history_ids") {
                    "history_related".to_owned()
                } else {
                    "shared_evidence".to_owned()
                },
                score: None,
                metadata: Some(json!({})),
            });
        }
    }
    output.into_iter().take(max_candidates).collect()
}

struct SeededRandom(u64);

impl SeededRandom {
    fn next_u64(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        self.0.rotate_right(17)
    }

    fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1_u64 << 53) as f64
    }

    fn gaussian(&mut self) -> f64 {
        let u1 = self.unit();
        let u2 = self.unit();
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }
}

pub fn select_embedding_exploration(
    nodes: &[SelectionNode],
    dimension: usize,
    cycle_seed: i64,
    max_candidates: usize,
    excluded_ids: Option<&BTreeSet<String>>,
) -> (Vec<MaintenanceCandidate>, &'static str) {
    let mut rng = SeededRandom(cycle_seed as u64);
    let probe = (0..dimension.max(1))
        .map(|_| rng.gaussian())
        .collect::<Vec<_>>();
    let excluded = excluded_ids.cloned().unwrap_or_default();
    let mut ranked = Vec::new();
    for node in nodes {
        let Some(node_vector) = vector(node) else {
            continue;
        };
        if node.id.is_empty() || excluded.contains(&node.id) || node_vector.len() != probe.len() {
            continue;
        }
        if let Some(score) = cosine(&probe, &node_vector) {
            ranked.push(MaintenanceCandidate {
                candidate_id: node.id.clone(),
                reason: "semantic_similar".to_owned(),
                score: Some(score),
                metadata: Some(json!({"probe_seed": cycle_seed})),
            });
        }
    }
    ranked.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(left.candidate_id.cmp(&right.candidate_id))
    });
    ranked.truncate(max_candidates);
    let strategy = if ranked.is_empty() {
        "embedding_unavailable"
    } else {
        "embedding_probe"
    };
    (ranked, strategy)
}
