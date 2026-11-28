use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SeedSource {
    pub id: String,
    pub title: String,
    pub url: String,
    pub revision: String,
    pub text: String,
    #[serde(default = "default_text_kind")]
    pub text_kind: String,
}

fn default_text_kind() -> String {
    "curated_paraphrase".to_owned()
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SeedMention {
    pub source_id: String,
    pub excerpt: String,
    pub start_char: Option<u64>,
    pub end_char: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum SeedPropertyValue {
    Strings(Vec<String>),
    Integer(i64),
    Float(f64),
    Boolean(bool),
    Text(String),
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SeedNode {
    pub id: String,
    pub label: String,
    pub kind: String,
    pub summary: String,
    pub mentions: Vec<SeedMention>,
    #[serde(default)]
    pub properties: BTreeMap<String, SeedPropertyValue>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SeedRelation {
    pub id: String,
    pub label: String,
    pub relation: String,
    pub summary: String,
    pub sources: Vec<String>,
    pub targets: Vec<String>,
    pub mentions: Vec<SeedMention>,
    #[serde(default)]
    pub properties: BTreeMap<String, SeedPropertyValue>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SeedAcceptanceQuery {
    pub query: String,
    #[serde(default)]
    pub expected_node_ids: Vec<String>,
    #[serde(default)]
    pub expected_relation_ids: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphSeedBundle {
    pub schema_version: u8,
    pub bundle_id: String,
    pub title: String,
    pub description: String,
    pub created_at: String,
    pub sources: Vec<SeedSource>,
    pub nodes: Vec<SeedNode>,
    #[serde(default)]
    pub edges: Vec<SeedRelation>,
    #[serde(default)]
    pub hyperedges: Vec<SeedRelation>,
    #[serde(default)]
    pub acceptance_queries: Vec<SeedAcceptanceQuery>,
}

fn unique_ids(kind: &str, values: &[String]) -> Result<BTreeSet<String>, String> {
    let unique = values.iter().cloned().collect::<BTreeSet<_>>();
    if unique.len() != values.len() {
        let mut counts = BTreeMap::new();
        for value in values {
            *counts.entry(value).or_insert(0_usize) += 1;
        }
        let duplicates = counts
            .into_iter()
            .filter(|(_, count)| *count > 1)
            .map(|(value, _)| value.clone())
            .collect::<Vec<_>>();
        return Err(format!("duplicate {kind} IDs: {duplicates:?}"));
    }
    Ok(unique)
}

fn source_characters(source: &SeedSource) -> Vec<char> {
    source.text.chars().collect()
}

fn find_excerpt(text: &[char], excerpt: &[char]) -> Option<usize> {
    if excerpt.is_empty() {
        return None;
    }
    if excerpt.len() > text.len() {
        return None;
    }
    (0..=text.len() - excerpt.len()).find(|offset| {
        text[*offset..*offset + excerpt.len()]
            .iter()
            .zip(excerpt)
            .all(|(left, right)| left == right)
    })
}

fn substring_by_chars(text: &str, start: usize, end: usize) -> String {
    text.chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}

fn resolve_mention(mention: &SeedMention, source: &SeedSource) -> Result<(u64, u64), String> {
    if let (Some(start), Some(end)) = (mention.start_char, mention.end_char) {
        let actual = substring_by_chars(&source.text, start as usize, end as usize);
        if actual != mention.excerpt {
            return Err(format!(
                "mention excerpt does not match half-open offsets in {}",
                source.id
            ));
        }
        return Ok((start, end));
    }
    let text = source_characters(source);
    let excerpt = mention.excerpt.chars().collect::<Vec<_>>();
    let first = find_excerpt(&text, &excerpt).ok_or_else(|| {
        format!(
            "mention excerpt is absent from {}: {:?}",
            source.id, mention.excerpt
        )
    })?;
    let second = find_excerpt(&text[first + 1..], &excerpt).map(|offset| first + 1 + offset);
    if second.is_some() {
        return Err(format!(
            "mention excerpt is ambiguous within {}: {:?}",
            source.id, mention.excerpt
        ));
    }
    Ok((first as u64, (first + excerpt.len()) as u64))
}

fn validate_entity(
    entity_id: &str,
    properties: &BTreeMap<String, SeedPropertyValue>,
    mentions: &[SeedMention],
    source_by_id: &BTreeMap<String, &SeedSource>,
) -> Result<(), String> {
    let reserved = properties
        .keys()
        .filter(|key| key.starts_with("_seed_"))
        .cloned()
        .collect::<Vec<_>>();
    if !reserved.is_empty() {
        return Err(format!(
            "{entity_id} uses reserved seed properties {reserved:?}"
        ));
    }
    for mention in mentions {
        let Some(source) = source_by_id.get(&mention.source_id) else {
            return Err(format!(
                "{entity_id} references unknown source {}",
                mention.source_id
            ));
        };
        resolve_mention(mention, source)?;
    }
    Ok(())
}

pub fn validate_bundle(bundle: &GraphSeedBundle) -> Result<(), String> {
    if bundle.schema_version != 1 {
        return Err("schema_version must be 1".to_owned());
    }
    if bundle.sources.is_empty() {
        return Err("sources must not be empty".to_owned());
    }
    if bundle.nodes.is_empty() {
        return Err("nodes must not be empty".to_owned());
    }
    let source_ids = unique_ids(
        "source",
        &bundle
            .sources
            .iter()
            .map(|item| item.id.clone())
            .collect::<Vec<_>>(),
    )?;
    let node_ids = unique_ids(
        "node",
        &bundle
            .nodes
            .iter()
            .map(|item| item.id.clone())
            .collect::<Vec<_>>(),
    )?;
    let relation_values = bundle
        .edges
        .iter()
        .chain(bundle.hyperedges.iter())
        .map(|item| item.id.clone())
        .collect::<Vec<_>>();
    let relation_ids = unique_ids("relation", &relation_values)?;
    let collisions = source_ids
        .intersection(&node_ids)
        .chain(source_ids.intersection(&relation_ids))
        .chain(node_ids.intersection(&relation_ids))
        .cloned()
        .collect::<BTreeSet<_>>();
    if !collisions.is_empty() {
        return Err(format!("IDs must be globally unique: {:?}", collisions));
    }

    let source_by_id = bundle
        .sources
        .iter()
        .map(|source| (source.id.clone(), source))
        .collect::<BTreeMap<_, _>>();
    for node in &bundle.nodes {
        validate_entity(&node.id, &node.properties, &node.mentions, &source_by_id)?;
    }
    for relation in bundle.edges.iter().chain(bundle.hyperedges.iter()) {
        validate_entity(
            &relation.id,
            &relation.properties,
            &relation.mentions,
            &source_by_id,
        )?;
    }

    let endpoints = source_ids
        .union(&node_ids)
        .cloned()
        .collect::<BTreeSet<_>>();
    for relation in bundle.edges.iter().chain(bundle.hyperedges.iter()) {
        let source_count = relation
            .sources
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>()
            .len();
        let target_count = relation
            .targets
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>()
            .len();
        if source_count != relation.sources.len() || target_count != relation.targets.len() {
            return Err(format!("{} contains duplicate endpoints", relation.id));
        }
        let missing = relation
            .sources
            .iter()
            .chain(relation.targets.iter())
            .filter(|endpoint| !endpoints.contains(*endpoint))
            .cloned()
            .collect::<BTreeSet<_>>();
        if !missing.is_empty() {
            return Err(format!(
                "{} references unknown endpoints {:?}",
                relation.id, missing
            ));
        }
    }
    for edge in &bundle.edges {
        if edge.sources.len() != 1 || edge.targets.len() != 1 {
            return Err(format!(
                "ordinary edge {} must have exactly one source and target",
                edge.id
            ));
        }
    }
    for hyperedge in &bundle.hyperedges {
        let endpoints = hyperedge
            .sources
            .iter()
            .chain(hyperedge.targets.iter())
            .cloned()
            .collect::<BTreeSet<_>>();
        if endpoints.len() < 3 {
            return Err(format!(
                "hyperedge {} must connect at least three distinct nodes",
                hyperedge.id
            ));
        }
    }
    for query in &bundle.acceptance_queries {
        let missing_nodes = query
            .expected_node_ids
            .iter()
            .filter(|id| !node_ids.contains(*id))
            .cloned()
            .collect::<BTreeSet<_>>();
        let missing_relations = query
            .expected_relation_ids
            .iter()
            .filter(|id| !relation_ids.contains(*id))
            .cloned()
            .collect::<BTreeSet<_>>();
        if !missing_nodes.is_empty() || !missing_relations.is_empty() {
            return Err(format!(
                "acceptance query '{}' references missing IDs: nodes={:?}, relations={:?}",
                query.query, missing_nodes, missing_relations
            ));
        }
    }
    Ok(())
}

fn canonical_mentions(
    mentions: &[SeedMention],
    source_by_id: &BTreeMap<String, &SeedSource>,
) -> Vec<SeedMention> {
    mentions
        .iter()
        .map(|mention| {
            let source = source_by_id
                .get(&mention.source_id)
                .copied()
                .expect("validated bundle references only known sources");
            let (start, end) =
                resolve_mention(mention, source).expect("validated bundle mentions resolve");
            SeedMention {
                source_id: mention.source_id.clone(),
                excerpt: mention.excerpt.clone(),
                start_char: Some(start),
                end_char: Some(end),
            }
        })
        .collect()
}

#[must_use]
pub fn canonicalized(bundle: &GraphSeedBundle) -> GraphSeedBundle {
    let source_by_id = bundle
        .sources
        .iter()
        .map(|source| (source.id.clone(), source))
        .collect::<BTreeMap<_, _>>();
    let mut canonical = bundle.clone();
    canonical
        .sources
        .sort_by(|left, right| left.id.cmp(&right.id));
    canonical
        .nodes
        .sort_by(|left, right| left.id.cmp(&right.id));
    canonical
        .edges
        .sort_by(|left, right| left.id.cmp(&right.id));
    canonical
        .hyperedges
        .sort_by(|left, right| left.id.cmp(&right.id));
    canonical
        .acceptance_queries
        .sort_by(|left, right| left.query.cmp(&right.query));
    for node in &mut canonical.nodes {
        node.mentions = canonical_mentions(&node.mentions, &source_by_id);
    }
    for relation in canonical
        .edges
        .iter_mut()
        .chain(canonical.hyperedges.iter_mut())
    {
        relation.mentions = canonical_mentions(&relation.mentions, &source_by_id);
    }
    canonical
}
