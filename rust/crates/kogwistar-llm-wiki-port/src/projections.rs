use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fmt;

use serde_json::Value;

use crate::configuration::workspace::{GraphSpace, GraphSpaceParseError, WorkspaceNamespaces};
use crate::models::JsonObject;

#[derive(Debug)]
pub enum ProjectionError {
    EmptyGraphSpaces,
    InvalidGraphSpace(GraphSpaceParseError),
    UnsupportedGraphSpace(GraphSpace),
}

impl fmt::Display for ProjectionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyGraphSpaces => write!(formatter, "graph_spaces must not be empty"),
            Self::InvalidGraphSpace(error) => write!(formatter, "{error}"),
            Self::UnsupportedGraphSpace(graph_space) => write!(
                formatter,
                "Projection does not support graph space '{}'",
                graph_space.as_str()
            ),
        }
    }
}

impl std::error::Error for ProjectionError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::EmptyGraphSpaces | Self::UnsupportedGraphSpace(_) => None,
            Self::InvalidGraphSpace(error) => Some(error),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum GraphSpaceInput<'a> {
    Space(GraphSpace),
    Text(&'a str),
}

impl AsRef<str> for GraphSpaceInput<'_> {
    fn as_ref(&self) -> &str {
        match self {
            Self::Space(graph_space) => graph_space.as_str(),
            Self::Text(value) => value,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectionNode {
    pub id: String,
    pub label: String,
    pub summary: String,
    pub metadata: JsonObject,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProjectionEdge {
    pub source_ids: Vec<String>,
    pub target_ids: Vec<String>,
}

pub fn normalize_graph_spaces(
    graph_spaces: Option<&[GraphSpaceInput<'_>]>,
) -> Result<Vec<GraphSpace>, ProjectionError> {
    let Some(graph_spaces) = graph_spaces else {
        return Ok(vec![GraphSpace::CuratedKg]);
    };

    let mut normalized = Vec::with_capacity(graph_spaces.len());
    for graph_space in graph_spaces {
        if let GraphSpaceInput::Space(graph_space) = graph_space {
            normalized.push(*graph_space);
            continue;
        }
        normalized.push(
            graph_space
                .as_ref()
                .trim()
                .to_ascii_lowercase()
                .parse::<GraphSpace>()
                .map_err(ProjectionError::InvalidGraphSpace)?,
        );
    }

    if normalized.is_empty() {
        return Err(ProjectionError::EmptyGraphSpaces);
    }
    Ok(normalized)
}

pub fn namespace_for_graph_space(
    namespaces: &WorkspaceNamespaces,
    graph_space: GraphSpace,
) -> Result<String, ProjectionError> {
    match graph_space {
        GraphSpace::Source => Ok(namespaces.source_space()),
        GraphSpace::BaseKg => Ok(namespaces.base_kg_space()),
        GraphSpace::CuratedKg => Ok(namespaces.curated_kg_space()),
        _ => Err(ProjectionError::UnsupportedGraphSpace(graph_space)),
    }
}

#[must_use]
pub fn node_graph_space(metadata: &JsonObject) -> String {
    metadata
        .get("graph_space")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase()
}

pub fn select_visible_nodes<'a>(
    all_nodes: &'a [ProjectionNode],
    requested_spaces: &[GraphSpace],
    manifest_ids: Option<&BTreeSet<String>>,
) -> Vec<&'a ProjectionNode> {
    let mut selected = Vec::new();
    let mut seen_ids = BTreeSet::new();
    let requested_space_values: HashSet<&str> = requested_spaces
        .iter()
        .map(|graph_space| graph_space.as_str())
        .collect();

    for node in all_nodes {
        if node.id.is_empty() || !seen_ids.insert(node.id.as_str()) {
            continue;
        }
        let node_space = node_graph_space(&node.metadata);
        if !requested_space_values.contains(node_space.as_str()) {
            continue;
        }
        if let Some(manifest_ids) = manifest_ids {
            if !manifest_ids.contains(&node.id) {
                continue;
            }
        }
        selected.push(node);
    }
    selected
}

#[must_use]
pub fn build_adjacency(edges: &[ProjectionEdge]) -> BTreeMap<String, BTreeSet<String>> {
    let mut adjacency: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for edge in edges {
        let edge_sources: Vec<&String> = edge
            .source_ids
            .iter()
            .filter(|source_id| !source_id.is_empty())
            .collect();
        let edge_targets: Vec<&String> = edge
            .target_ids
            .iter()
            .filter(|target_id| !target_id.is_empty())
            .collect();

        for source_id in &edge_sources {
            adjacency
                .entry((*source_id).clone())
                .or_default()
                .extend(edge_targets.iter().map(|target_id| (*target_id).clone()));
        }
        for target_id in &edge_targets {
            adjacency
                .entry((*target_id).clone())
                .or_default()
                .extend(edge_sources.iter().map(|source_id| (*source_id).clone()));
        }
    }
    adjacency
}
