use std::collections::{BTreeMap, BTreeSet};
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

use crate::provenance::python_ascii_json;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[must_use]
fn json_int(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(number)) => number
            .as_f64()
            .map(|value| value as i64)
            .unwrap_or_default(),
        Some(Value::String(value)) => value.trim().parse().unwrap_or_default(),
        _ => 0,
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StageTiming {
    pub count: i64,
    pub total_ms: i64,
    pub average_ms: Option<f64>,
    pub max_ms: Option<i64>,
    pub open_count: i64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StageTimingSummary {
    pub stage_count: usize,
    pub stages: BTreeMap<String, StageTiming>,
    pub dominant_stage: Option<String>,
    pub dominant_stage_total_ms: i64,
    pub dominant_operation_stage: Option<String>,
    pub dominant_operation_stage_total_ms: i64,
}

const TERMINAL_SUFFIXES: [&str; 6] = [
    "_complete",
    "_completed",
    "_done",
    "_returned",
    "_failed",
    "_skipped",
];

#[must_use]
pub fn summarize_stage_timings(layer_log: &[Value]) -> StageTimingSummary {
    let mut starts: BTreeMap<String, Vec<i64>> = BTreeMap::new();
    let mut durations: BTreeMap<String, Vec<i64>> = BTreeMap::new();

    for event in layer_log {
        let stage = event
            .get("stage")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let Some(timestamp) = event.get("timestamp_ms").and_then(Value::as_i64) else {
            continue;
        };
        if stage.is_empty() {
            continue;
        }
        if let Some(base) = stage.strip_suffix("_start") {
            starts.entry(base.to_owned()).or_default().push(timestamp);
            continue;
        }
        let Some(base) = TERMINAL_SUFFIXES
            .iter()
            .find_map(|suffix| stage.strip_suffix(suffix))
        else {
            continue;
        };
        if let Some(pending) = starts.get_mut(base) {
            if let Some(started_at) = pending.pop() {
                durations
                    .entry(base.to_owned())
                    .or_default()
                    .push((timestamp - started_at).max(0));
            }
        }
    }

    let mut stages = BTreeMap::<String, StageTiming>::new();
    let keys = starts
        .keys()
        .cloned()
        .chain(durations.keys().cloned())
        .collect::<BTreeSet<_>>();
    for base in keys {
        let values = durations.get(&base).cloned().unwrap_or_default();
        let open_count = starts.get(&base).map(Vec::len).unwrap_or_default() as i64;
        stages.insert(
            base,
            StageTiming {
                count: values.len() as i64,
                total_ms: values.iter().sum(),
                average_ms: if values.is_empty() {
                    None
                } else {
                    Some(
                        (values.iter().sum::<i64>() as f64 / values.len() as f64 * 100.0).round()
                            / 100.0,
                    )
                },
                max_ms: values.iter().max().copied(),
                open_count,
            },
        );
    }

    let mut ranked = stages
        .iter()
        .map(|(name, timing)| (name.clone(), timing.clone()))
        .collect::<Vec<_>>();
    ranked.sort_by(|left, right| {
        (right.1.total_ms, right.0.as_str()).cmp(&(left.1.total_ms, left.0.as_str()))
    });
    let operation_ranked = ranked
        .iter()
        .filter(|(name, _)| {
            !name.ends_with("_parse") && name != "parse" && name != "workflow_layered_parse"
        })
        .cloned()
        .collect::<Vec<_>>();
    StageTimingSummary {
        stage_count: stages.len(),
        stages,
        dominant_stage: ranked
            .first()
            .filter(|(_, timing)| json_int(Some(&Value::from(timing.total_ms))) > 0)
            .map(|(name, _)| name.clone()),
        dominant_stage_total_ms: ranked
            .first()
            .map(|(_, timing)| timing.total_ms)
            .unwrap_or_default(),
        dominant_operation_stage: operation_ranked.first().map(|(name, _)| name.clone()),
        dominant_operation_stage_total_ms: operation_ranked
            .first()
            .map(|(_, timing)| timing.total_ms)
            .unwrap_or_default(),
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AggregatedStageTiming {
    pub count: i64,
    pub total_ms: i64,
    pub open_count: i64,
    pub average_ms: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AggregateStageTimingSummary {
    pub stages: BTreeMap<String, AggregatedStageTiming>,
    pub dominant_stage: Option<String>,
    pub dominant_stage_total_ms: i64,
    pub dominant_operation_stage: Option<String>,
    pub dominant_operation_stage_total_ms: i64,
}

#[must_use]
pub fn aggregate_stage_timings(summaries: &[Value]) -> AggregateStageTimingSummary {
    let mut stages: BTreeMap<String, AggregatedStageTiming> = BTreeMap::new();
    for summary in summaries {
        let Some(Value::Object(raw_stages)) = summary.get("stages") else {
            continue;
        };
        for (name, raw_values) in raw_stages {
            let Some(Value::Object(values)) = Some(raw_values) else {
                continue;
            };
            let target = stages
                .entry(name.clone())
                .or_insert_with(|| AggregatedStageTiming {
                    count: 0,
                    total_ms: 0,
                    open_count: 0,
                    average_ms: 0.0,
                });
            target.count += json_int(values.get("count"));
            target.total_ms += json_int(values.get("total_ms"));
            target.open_count += json_int(values.get("open_count"));
        }
    }
    for values in stages.values_mut() {
        values.average_ms = if values.count == 0 {
            0.0
        } else {
            (values.total_ms as f64 / values.count as f64 * 100.0).round() / 100.0
        };
    }
    let ranked = stages
        .iter()
        .map(|(name, timing)| (name.clone(), timing.clone()))
        .collect::<Vec<_>>();
    let ranked = {
        let mut ranked = ranked;
        ranked.sort_by(|left, right| {
            (right.1.total_ms, right.0.as_str()).cmp(&(left.1.total_ms, left.0.as_str()))
        });
        ranked
    };
    let operation_ranked = ranked
        .iter()
        .filter(|(name, _)| {
            !name.ends_with("_parse") && name != "parse" && name != "workflow_layered_parse"
        })
        .cloned()
        .collect::<Vec<_>>();
    AggregateStageTimingSummary {
        stages,
        dominant_stage: ranked.first().map(|(name, _)| name.clone()),
        dominant_stage_total_ms: ranked
            .first()
            .map(|(_, timing)| timing.total_ms)
            .unwrap_or_default(),
        dominant_operation_stage: operation_ranked.first().map(|(name, _)| name.clone()),
        dominant_operation_stage_total_ms: operation_ranked
            .first()
            .map(|(_, timing)| timing.total_ms)
            .unwrap_or_default(),
    }
}

pub fn append_jsonl(path: &Path, payload: &Value) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", python_ascii_json(payload))
}

#[must_use]
pub fn semantic_tree_depth(node: &Value) -> usize {
    let children = node
        .get("child_nodes")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    if children.is_empty() {
        1
    } else {
        1 + children
            .iter()
            .map(semantic_tree_depth)
            .max()
            .unwrap_or_default()
    }
}

#[must_use]
pub fn semantic_tree_node_count(node: &Value) -> usize {
    let children = node
        .get("child_nodes")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    1 + children.iter().map(semantic_tree_node_count).sum::<usize>()
}

#[must_use]
pub fn semantic_tree_leaf_count(node: &Value) -> usize {
    let children = node
        .get("child_nodes")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    if children.is_empty() {
        1
    } else {
        children.iter().map(semantic_tree_leaf_count).sum()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SemanticTreeSummary {
    pub tree_depth: usize,
    pub tree_node_count: usize,
    pub tree_leaf_count: usize,
}

#[must_use]
pub fn summarize_semantic_tree(tree: &Value) -> SemanticTreeSummary {
    SemanticTreeSummary {
        tree_depth: semantic_tree_depth(tree),
        tree_node_count: semantic_tree_node_count(tree),
        tree_leaf_count: semantic_tree_leaf_count(tree),
    }
}
