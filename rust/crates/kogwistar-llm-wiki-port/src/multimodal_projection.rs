/// Multimodal capture and projection contracts.
///
/// Mirrors Python contract `src/kogwistar_llm_wiki/embeddings/multimodal_projection.py`.
/// Stores multimodal retrieval views separately from single-vector graph embeddings,
/// letting ColQwen-style encoders return one vector per token or image patch without
/// changing the graph schema. Self-contained: no external engine or embedding contract
/// crates required; all types defined within this port.
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Modality of a multimodal source unit.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceModality {
    Text,
    Image,
    Audio,
    Video,
    Webpage,
}

impl SourceModality {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Image => "image",
            Self::Audio => "audio",
            Self::Video => "video",
            Self::Webpage => "webpage",
        }
    }
}

/// Similarity metric for embedding search.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SimilarityMetric {
    Cosine,
    L2,
    DotProduct,
}

impl SimilarityMetric {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Cosine => "cosine",
            Self::L2 => "l2",
            Self::DotProduct => "dot_product",
        }
    }
}

/// Multimodal embedding profile.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MultimodalEmbeddingProfile {
    pub model: String,
    pub revision: Option<String>,
    pub dimension: usize,
    pub metric: SimilarityMetric,
    pub max_sequence_length: Option<usize>,
    pub crop_token_budget: Option<usize>,
}

impl MultimodalEmbeddingProfile {
    #[must_use]
    pub fn fingerprint(&self) -> String {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut hasher = DefaultHasher::new();
        self.model.hash(&mut hasher);
        self.revision.hash(&mut hasher);
        self.dimension.hash(&mut hasher);
        self.metric.as_str().hash(&mut hasher);
        format!("{:x}", hasher.finish())
    }
}

/// A source unit captured for multimodal projection (Stage 1 metadata).
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MultimodalSourceUnit {
    pub view_id: String,
    pub workspace_id: String,
    pub modality: SourceModality,
    pub content_ref: Option<String>,
    pub asset_sha256: Option<String>,
    pub embedding_profile: Option<String>,
}

impl MultimodalSourceUnit {
    #[must_use]
    pub fn new(view_id: &str, workspace_id: &str, modality: SourceModality) -> Self {
        Self {
            view_id: view_id.to_owned(),
            workspace_id: workspace_id.to_owned(),
            modality,
            content_ref: None,
            asset_sha256: None,
            embedding_profile: None,
        }
    }
}

/// A set of embeddings for one source unit.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EmbeddingSet {
    pub view_id: String,
    pub profile_fingerprint: String,
    pub vectors: Vec<Vec<f64>>,
}

/// Error type for projection integrity violations.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProjectionIntegrityError(pub String);

impl std::fmt::Display for ProjectionIntegrityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "projection integrity error: {}", self.0)
    }
}

impl std::error::Error for ProjectionIntegrityError {}

/// A search hit from a multimodal projection store.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MultimodalSearchHit {
    pub view_id: String,
    pub score: f64,
    pub metadata: BTreeMap<String, serde_json::Value>,
}

/// Multimodal encoder: produces embedding sets for source units.
pub trait MultimodalEncoder: Send + Sync {
    fn profile(&self) -> &MultimodalEmbeddingProfile;
    fn encode_documents(
        &self,
        units: &[MultimodalSourceUnit],
    ) -> Result<Vec<Vec<Vec<f64>>>, ProjectionIntegrityError>;
}

/// Multimodal projection store: stores and queries embedding sets.
pub trait MultimodalProjectionStore: Send + Sync {
    fn upsert_embedding(
        &mut self,
        unit: &MultimodalSourceUnit,
        embedding: Vec<Vec<f64>>,
        profile: &MultimodalEmbeddingProfile,
    ) -> Result<(), ProjectionIntegrityError>;
    fn pending_units(&self, workspace_id: Option<&str>) -> Vec<MultimodalSourceUnit>;
    fn search(
        &self,
        query: &[f64],
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Vec<MultimodalSearchHit>;
}

/// In-memory multimodal projection store.
#[derive(Clone, Debug, Default)]
pub struct InMemoryMultimodalProjectionStore {
    pub embeddings: BTreeMap<
        String,
        (
            MultimodalSourceUnit,
            Vec<Vec<f64>>,
            MultimodalEmbeddingProfile,
        ),
    >,
    pub workspace_index: BTreeMap<String, Vec<String>>,
}

impl InMemoryMultimodalProjectionStore {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }
}

impl MultimodalProjectionStore for InMemoryMultimodalProjectionStore {
    fn upsert_embedding(
        &mut self,
        unit: &MultimodalSourceUnit,
        embedding: Vec<Vec<f64>>,
        profile: &MultimodalEmbeddingProfile,
    ) -> Result<(), ProjectionIntegrityError> {
        let key = unit.view_id.clone();
        self.embeddings
            .insert(key.clone(), (unit.clone(), embedding, profile.clone()));
        self.workspace_index
            .entry(unit.workspace_id.clone())
            .or_default()
            .push(key);
        Ok(())
    }

    fn pending_units(&self, workspace_id: Option<&str>) -> Vec<MultimodalSourceUnit> {
        self.embeddings
            .values()
            .filter(|(unit, _, _)| workspace_id.map_or(true, |w| unit.workspace_id == w))
            .map(|(unit, _, _)| unit.clone())
            .collect()
    }

    fn search(
        &self,
        query: &[f64],
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Vec<MultimodalSearchHit> {
        let mut hits: Vec<(String, f64)> = self
            .embeddings
            .iter()
            .filter(|(_, (unit, _, _))| workspace_id.map_or(true, |w| unit.workspace_id == w))
            .map(|(view_id, (_, vectors, _))| {
                // Simple cosine similarity against the first vector
                let score = if let Some(vec) = vectors.first() {
                    cosine_similarity(query, vec)
                } else {
                    0.0
                };
                (view_id.clone(), score)
            })
            .collect();
        hits.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        hits.into_iter()
            .take(limit)
            .map(|(view_id, score)| MultimodalSearchHit {
                view_id,
                score,
                metadata: BTreeMap::new(),
            })
            .collect()
    }
}

/// Fake multimodal encoder for provider-free tests.
#[derive(Clone, Debug, Default)]
pub struct FakeMultimodalEncoder;

impl MultimodalEncoder for FakeMultimodalEncoder {
    fn profile(&self) -> &MultimodalEmbeddingProfile {
        static PROFILE: std::sync::OnceLock<MultimodalEmbeddingProfile> =
            std::sync::OnceLock::new();
        PROFILE.get_or_init(|| MultimodalEmbeddingProfile {
            model: "kogwistar-fake-multimodal-v1".to_owned(),
            revision: None,
            dimension: 2,
            metric: SimilarityMetric::Cosine,
            max_sequence_length: None,
            crop_token_budget: None,
        })
    }

    fn encode_documents(
        &self,
        units: &[MultimodalSourceUnit],
    ) -> Result<Vec<Vec<Vec<f64>>>, ProjectionIntegrityError> {
        Ok(units
            .iter()
            .map(|unit| {
                let text = unit.view_id.as_bytes();
                let checksum: f64 = (text.iter().map(|&b| b as usize).sum::<usize>() % 97 + 1) as f64;
                vec![vec![(unit.view_id.len() + 1) as f64, checksum]]
            })
            .collect())
    }
}

/// Compute cosine similarity between two vectors.
fn cosine_similarity(a: &[f64], b: &[f64]) -> f64 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }
    let dot: f64 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let norm_a: f64 = a.iter().map(|x| x * x).sum::<f64>().sqrt();
    let norm_b: f64 = b.iter().map(|x| x * x).sum::<f64>().sqrt();
    if norm_a == 0.0 || norm_b == 0.0 {
        0.0
    } else {
        dot / (norm_a * norm_b)
    }
}

/// Promote captured Stage-1 units incrementally and crash-safely.
pub fn embed_pending(
    store: &mut dyn MultimodalProjectionStore,
    encoder: &dyn MultimodalEncoder,
    workspace_id: Option<&str>,
    max_units: Option<usize>,
) -> Result<usize, ProjectionIntegrityError> {
    if let Some(max) = max_units {
        if max == 0 {
            return Err(ProjectionIntegrityError(
                "max_units must be positive when supplied".to_owned(),
            ));
        }
    }

    let mut pending = store.pending_units(workspace_id);
    if let Some(max) = max_units {
        pending.truncate(max);
    }
    let pending_count = pending.len();
    if pending_count == 0 {
        return Ok(0);
    }

    let vectors = encoder.encode_documents(&pending)?;
    if vectors.len() != pending_count {
        return Err(ProjectionIntegrityError(
            "encoder returned a different number of document embeddings".to_owned(),
        ));
    }

    let profile = encoder.profile();
    for (unit, embedding) in pending.into_iter().zip(vectors) {
        store.upsert_embedding(&unit, embedding, profile)?;
    }

    Ok(pending_count)
}

/// Score embedding sets against a query vector.
pub fn score_embedding_sets(sets: &[EmbeddingSet], query: &[f64]) -> Vec<(String, f64)> {
    let mut scores: Vec<(String, f64)> = sets
        .iter()
        .map(|set| {
            let score = set
                .vectors
                .iter()
                .map(|v| cosine_similarity(query, v))
                .fold(0.0f64, f64::max);
            (set.view_id.clone(), score)
        })
        .collect();
    scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    scores
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_modality_as_str() {
        assert_eq!(SourceModality::Text.as_str(), "text");
        assert_eq!(SourceModality::Image.as_str(), "image");
        assert_eq!(SourceModality::Video.as_str(), "video");
    }

    #[test]
    fn similarity_metric_as_str() {
        assert_eq!(SimilarityMetric::Cosine.as_str(), "cosine");
        assert_eq!(SimilarityMetric::L2.as_str(), "l2");
    }

    #[test]
    fn multimodal_source_unit_new() {
        let unit = MultimodalSourceUnit::new("view-1", "ws-1", SourceModality::Image);
        assert_eq!(unit.view_id, "view-1");
        assert_eq!(unit.workspace_id, "ws-1");
        assert_eq!(unit.modality, SourceModality::Image);
        assert!(unit.content_ref.is_none());
        assert!(unit.asset_sha256.is_none());
    }

    #[test]
    fn embedding_profile_fingerprint() {
        let profile = MultimodalEmbeddingProfile {
            model: "colqwen-v1".to_owned(),
            revision: Some("rev-1".to_owned()),
            dimension: 128,
            metric: SimilarityMetric::Cosine,
            max_sequence_length: Some(8192),
            crop_token_budget: Some(2048),
        };
        let fp = profile.fingerprint();
        assert!(!fp.is_empty());
        // Same profile => same fingerprint
        assert_eq!(fp, profile.fingerprint());
    }

    #[test]
    fn fake_encoder_profile() {
        let encoder = FakeMultimodalEncoder;
        let profile = encoder.profile();
        assert_eq!(profile.model, "kogwistar-fake-multimodal-v1");
        assert_eq!(profile.dimension, 2);
    }

    #[test]
    fn fake_encoder_encode_documents() {
        let encoder = FakeMultimodalEncoder;
        let units = vec![
            MultimodalSourceUnit::new("view-a", "ws-1", SourceModality::Text),
            MultimodalSourceUnit::new("view-b", "ws-1", SourceModality::Image),
        ];
        let vectors = encoder.encode_documents(&units).unwrap();
        assert_eq!(vectors.len(), 2);
        assert_eq!(vectors[0].len(), 1);
        assert_eq!(vectors[0][0].len(), 2);
        assert_ne!(vectors[0], vectors[1]);
    }

    #[test]
    fn in_memory_store_upsert_and_search() {
        let mut store = InMemoryMultimodalProjectionStore::new();
        let encoder = FakeMultimodalEncoder;
        let profile = encoder.profile().clone();

        let unit = MultimodalSourceUnit::new("view-1", "ws-1", SourceModality::Text);
        store
            .upsert_embedding(&unit, vec![vec![1.0, 2.0]], &profile)
            .unwrap();

        let pending = store.pending_units(Some("ws-1"));
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].view_id, "view-1");

        let hits = store.search(&[1.0, 2.0], Some("ws-1"), 5);
        assert!(!hits.is_empty());
        assert_eq!(hits[0].view_id, "view-1");
    }

    #[test]
    fn in_memory_store_search_empty_workspace() {
        let mut store = InMemoryMultimodalProjectionStore::new();
        let encoder = FakeMultimodalEncoder;
        let profile = encoder.profile().clone();

        let unit = MultimodalSourceUnit::new("view-1", "ws-1", SourceModality::Text);
        store
            .upsert_embedding(&unit, vec![vec![1.0, 2.0]], &profile)
            .unwrap();

        let hits = store.search(&[1.0, 2.0], Some("ws-other"), 5);
        assert_eq!(hits.len(), 0);
    }

    #[test]
    fn embed_pending_no_units() {
        let mut store = InMemoryMultimodalProjectionStore::new();
        let encoder = FakeMultimodalEncoder;
        let result = embed_pending(&mut store, &encoder, None, None);
        assert_eq!(result.unwrap(), 0);
    }

    #[test]
    fn embed_pending_promotes_units() {
        let mut store = InMemoryMultimodalProjectionStore::new();
        let encoder = FakeMultimodalEncoder;
        // First upsert to create "pending" units
        let unit = MultimodalSourceUnit::new("view-1", "ws-1", SourceModality::Text);
        let profile = encoder.profile().clone();
        store
            .upsert_embedding(&unit, vec![vec![0.0, 0.0]], &profile)
            .unwrap();

        let result = embed_pending(&mut store, &encoder, None, None);
        assert_eq!(result.unwrap(), 1);
    }

    #[test]
    fn embed_pending_max_units_zero_rejected() {
        let mut store = InMemoryMultimodalProjectionStore::new();
        let encoder = FakeMultimodalEncoder;
        let result = embed_pending(&mut store, &encoder, None, Some(0));
        assert!(result.is_err());
    }

    #[test]
    fn score_embedding_sets_ranks_by_similarity() {
        let sets = vec![
            EmbeddingSet {
                view_id: "a".to_owned(),
                profile_fingerprint: "fp".to_owned(),
                vectors: vec![vec![1.0, 0.0]],
            },
            EmbeddingSet {
                view_id: "b".to_owned(),
                profile_fingerprint: "fp".to_owned(),
                vectors: vec![vec![0.0, 1.0]],
            },
        ];
        let scores = score_embedding_sets(&sets, &[1.0, 0.0]);
        assert_eq!(scores[0].0, "a");
        assert_eq!(scores[1].0, "b");
        assert!(scores[0].1 > scores[1].1);
    }

    #[test]
    fn cosine_similarity_identical_vectors() {
        let v = vec![1.0, 2.0, 3.0];
        assert!((cosine_similarity(&v, &v) - 1.0).abs() < 1e-10);
    }

    #[test]
    fn cosine_similarity_orthogonal_vectors() {
        let a = vec![1.0, 0.0];
        let b = vec![0.0, 1.0];
        assert!((cosine_similarity(&a, &b) - 0.0).abs() < 1e-10);
    }
}
