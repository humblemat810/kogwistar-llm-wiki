//! Authenticated HTTP adapter for the external multimodal service.
//!
//! Mirrors Python contract `src/kogwistar_llm_wiki/embeddings/multimodal_remote.py`.
//! The adapter sends resolved asset bytes only, never source paths or URLs.

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::fmt;

const CONTRACT_VERSION: &str = "1";

use crate::multimodal_projection::{
    EmbeddingSet, MultimodalEmbeddingProfile, MultimodalEncoder, MultimodalSourceUnit,
    ProjectionIntegrityError, SourceModality,
};
/// Base error for remote embedding calls.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EmbeddingServiceError {
    /// A transient network or service availability failure.
    Unavailable(String),
    /// A non-retryable contract, authentication, or profile failure.
    Protocol(String),
}

impl EmbeddingServiceError {
    #[must_use]
    pub fn unavailable(message: impl Into<String>) -> Self {
        Self::Unavailable(message.into())
    }

    #[must_use]
    pub fn protocol(message: impl Into<String>) -> Self {
        Self::Protocol(message.into())
    }

    #[must_use]
    pub fn is_unavailable(&self) -> bool {
        matches!(self, Self::Unavailable(_))
    }

    #[must_use]
    pub fn is_protocol(&self) -> bool {
        matches!(self, Self::Protocol(_))
    }
}

impl fmt::Display for EmbeddingServiceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unavailable(message) => {
                write!(formatter, "embedding service unavailable: {message}")
            }
            Self::Protocol(message) => write!(formatter, "embedding protocol error: {message}"),
        }
    }
}

impl std::error::Error for EmbeddingServiceError {}

/// Minimal response surface required by the remote embedding adapters.
pub trait HttpResponseLike {
    fn status(&self) -> u16;
    fn body(&self) -> &[u8];
}

/// Injectable HTTP opener used by production and deterministic tests.
pub trait UrlOpener: Send + Sync {
    fn open(
        &self,
        url: &str,
        timeout_seconds: f64,
    ) -> Result<Box<dyn HttpResponseLike>, EmbeddingServiceError>;
}

/// Transport limits and authentication for one embedding service.
#[derive(Clone, Debug, PartialEq)]
pub struct EmbeddingServiceSettings {
    pub url: String,
    pub token: Option<String>,
    pub timeout_seconds: f64,
    pub max_request_bytes: usize,
    pub expected_profile_fingerprint: Option<String>,
    pub allowed_hosts: Vec<String>,
}

impl EmbeddingServiceSettings {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        url: impl Into<String>,
        token: Option<String>,
        timeout_seconds: f64,
        max_request_bytes: usize,
        expected_profile_fingerprint: Option<String>,
        allowed_hosts: Vec<String>,
    ) -> Result<Self, EmbeddingServiceError> {
        let url = url.into();
        if !url.starts_with("http://") && !url.starts_with("https://") {
            return Err(EmbeddingServiceError::protocol(
                "embedding service URL must use HTTP(S)".to_string(),
            ));
        }
        if timeout_seconds <= 0.0 || max_request_bytes == 0 {
            return Err(EmbeddingServiceError::protocol(
                "embedding service limits must be positive".to_string(),
            ));
        }
        let hostname = url
            .strip_prefix("http://")
            .or_else(|| url.strip_prefix("https://"))
            .and_then(|rest| rest.split('/').next())
            .unwrap_or("")
            .to_lowercase();
        if !allowed_hosts.is_empty()
            && !allowed_hosts
                .iter()
                .any(|host| host.to_lowercase() == hostname)
        {
            return Err(EmbeddingServiceError::protocol(format!(
                "embedding service host {hostname:?} is not allowlisted"
            )));
        }
        Ok(Self {
            url,
            token,
            timeout_seconds,
            max_request_bytes,
            expected_profile_fingerprint,
            allowed_hosts,
        })
    }
}

/// Resolves a source unit into asset bytes.
pub trait AssetResolver: Send + Sync {
    fn resolve(&self, unit: &MultimodalSourceUnit) -> Result<Vec<u8>, ProjectionIntegrityError>;
}

fn base64_encode(input: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in input.chunks(3) {
        let a = chunk[0];
        let b = chunk.get(1).copied().unwrap_or(0);
        let c = chunk.get(2).copied().unwrap_or(0);
        let triple = ((a as u32) << 16) | ((b as u32) << 8) | (c as u32);
        out.push(ALPHABET[((triple >> 18) & 63) as usize] as char);
        out.push(ALPHABET[((triple >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[((triple >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[(triple & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn default_content_type(unit: &MultimodalSourceUnit) -> &'static str {
    if matches!(
        unit.modality,
        SourceModality::Image | SourceModality::Webpage
    ) {
        "image/png"
    } else {
        "application/octet-stream"
    }
}

fn item_payload(
    unit: &MultimodalSourceUnit,
    resolver: Option<&dyn AssetResolver>,
) -> Result<Map<String, Value>, ProjectionIntegrityError> {
    let mut item = Map::new();
    item.insert("item_id".to_string(), Value::from(unit.view_id.clone()));
    item.insert("modality".to_string(), Value::from(unit.modality.as_str()));
    if let Some(content_ref) = &unit.content_ref {
        let _ = content_ref;
        let resolver = resolver.ok_or_else(|| {
            ProjectionIntegrityError(format!(
                "asset resolver is required for remote unit {}",
                unit.view_id
            ))
        })?;
        let raw = resolver.resolve(unit)?;
        let digest = sha256_hex(&raw);
        if let Some(expected) = &unit.asset_sha256 {
            if expected.to_lowercase() != digest {
                return Err(ProjectionIntegrityError(format!(
                    "asset hash mismatch for {}: expected {expected}, got {digest}",
                    unit.view_id
                )));
            }
        }
        let content_type = unit
            .metadata
            .get("content_type")
            .and_then(Value::as_str)
            .unwrap_or(default_content_type(unit));
        item.insert(
            "asset".to_string(),
            Value::Object(Map::from_iter([
                ("content_type".to_string(), Value::from(content_type)),
                ("sha256".to_string(), Value::from(digest)),
                ("data_base64".to_string(), Value::from(base64_encode(&raw))),
            ])),
        );
    }
    Ok(item)
}

fn normalise_embedding_set(
    value: &Value,
    dimension: usize,
) -> Result<Vec<Vec<f64>>, EmbeddingServiceError> {
    let Value::Array(vectors) = value.clone() else {
        return Err(EmbeddingServiceError::protocol(
            "embedding vectors must be a sequence".to_string(),
        ));
    };
    let mut result = Vec::with_capacity(vectors.len());
    for vector in vectors {
        let Value::Array(numbers) = vector.clone() else {
            return Err(EmbeddingServiceError::protocol(
                "embedding vector must be a sequence".to_string(),
            ));
        };
        let mut row = Vec::with_capacity(numbers.len());
        for number in numbers {
            let Some(finite) = number.as_f64().filter(|value| value.is_finite()) else {
                return Err(EmbeddingServiceError::protocol(
                    "embedding vector contains a non-finite value".to_string(),
                ));
            };
            row.push(finite);
        }
        if row.len() != dimension {
            return Err(EmbeddingServiceError::protocol(format!(
                "embedding vector dimension {} does not match expected {dimension}",
                row.len()
            )));
        }
        result.push(row);
    }
    Ok(result)
}

/// Remote multimodal encoder backed by an HTTP embedding service.
pub struct RemoteMultimodalEncoder {
    settings: EmbeddingServiceSettings,
    opener: Box<dyn UrlOpener>,
    profile: MultimodalEmbeddingProfile,
    resolver: Option<Box<dyn AssetResolver>>,
}

impl RemoteMultimodalEncoder {
    pub fn new(
        settings: EmbeddingServiceSettings,
        opener: Box<dyn UrlOpener>,
        profile: MultimodalEmbeddingProfile,
        resolver: Option<Box<dyn AssetResolver>>,
    ) -> Self {
        Self {
            settings,
            opener,
            profile,
            resolver,
        }
    }

    fn profile_payload(&self) -> Value {
        Value::Object(Map::from_iter([
            ("model".to_string(), Value::from(self.profile.model.clone())),
            (
                "revision".to_string(),
                Value::from(self.profile.revision.clone().unwrap_or_default()),
            ),
            ("dimension".to_string(), Value::from(self.profile.dimension)),
            (
                "metric".to_string(),
                Value::from(self.profile.metric.as_str()),
            ),
            (
                "max_sequence_length".to_string(),
                self.profile
                    .max_sequence_length
                    .map(Value::from)
                    .unwrap_or(Value::Null),
            ),
            (
                "crop_token_budget".to_string(),
                self.profile
                    .crop_token_budget
                    .map(Value::from)
                    .unwrap_or(Value::Null),
            ),
        ]))
    }

    fn request(
        &self,
        _kind: &str,
        items: Vec<Map<String, Value>>,
    ) -> Result<Vec<EmbeddingSet>, EmbeddingServiceError> {
        let body = Value::Object(Map::from_iter([
            (
                "contract_version".to_string(),
                Value::from(CONTRACT_VERSION),
            ),
            ("profile".to_string(), self.profile_payload()),
            (
                "items".to_string(),
                Value::Array(items.clone().into_iter().map(Value::Object).collect()),
            ),
        ]));
        let encoded = serde_json::to_string(&body).expect("request body is serializable");
        if encoded.len() > self.settings.max_request_bytes {
            return Err(EmbeddingServiceError::protocol(
                "embedding service request exceeds max_request_bytes".to_string(),
            ));
        }
        let response = self
            .opener
            .open(&self.settings.url, self.settings.timeout_seconds)?;
        let status = response.status();
        if status >= 500 {
            return Err(EmbeddingServiceError::unavailable(format!(
                "embedding service HTTP {status}"
            )));
        }
        let raw = response.body();
        let result: Value = serde_json::from_slice(raw).map_err(|_| {
            EmbeddingServiceError::protocol("embedding service returned invalid JSON".to_string())
        })?;
        let Some(object) = result.as_object() else {
            return Err(EmbeddingServiceError::protocol(
                "embedding response must be an object".to_string(),
            ));
        };
        if object.get("contract_version") != Some(&Value::from(CONTRACT_VERSION)) {
            return Err(EmbeddingServiceError::protocol(
                "embedding contract version mismatch".to_string(),
            ));
        }
        let Some(Value::Object(profile_payload)) = object.get("profile") else {
            return Err(EmbeddingServiceError::protocol(
                "embedding response omitted profile".to_string(),
            ));
        };
        let returned_fingerprint = profile_payload
            .get("fingerprint")
            .and_then(Value::as_str)
            .unwrap_or("");
        if returned_fingerprint != self.profile.fingerprint() {
            return Err(EmbeddingServiceError::protocol(
                "embedding profile fingerprint mismatch".to_string(),
            ));
        }
        if let Some(expected) = &self.settings.expected_profile_fingerprint {
            if returned_fingerprint != expected.as_str() {
                return Err(EmbeddingServiceError::protocol(
                    "embedding expected profile fingerprint mismatch".to_string(),
                ));
            }
        }
        let Some(Value::Array(raw_results)) = object.get("results") else {
            return Err(EmbeddingServiceError::protocol(
                "embedding response results must be a sequence".to_string(),
            ));
        };
        let expected_ids: Vec<String> = items
            .iter()
            .map(|item| {
                item.get("item_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string()
            })
            .collect();
        if raw_results.len() != expected_ids.len() {
            return Err(EmbeddingServiceError::protocol(
                "embedding response count does not match request".to_string(),
            ));
        }
        let mut vectors = Vec::with_capacity(raw_results.len());
        for (expected_id, raw_item) in expected_ids.iter().zip(raw_results) {
            let Value::Object(item) = raw_item.clone() else {
                return Err(EmbeddingServiceError::protocol(format!(
                    "embedding response changed item identity for {expected_id}"
                )));
            };
            if item.get("item_id").and_then(Value::as_str) != Some(expected_id.as_str()) {
                return Err(EmbeddingServiceError::protocol(format!(
                    "embedding response changed item ordering for {expected_id}"
                )));
            }
            let Some(Value::Array(raw_vectors)) = item.get("vectors") else {
                return Err(EmbeddingServiceError::protocol(format!(
                    "invalid embedding vector for item {expected_id}"
                )));
            };
            let normalized = normalise_embedding_set(
                &Value::Array(raw_vectors.clone()),
                self.profile.dimension,
            )?;
            vectors.push(EmbeddingSet {
                view_id: expected_id.clone(),
                profile_fingerprint: self.profile.fingerprint(),
                vectors: normalized,
            });
        }
        Ok(vectors)
    }
}

impl MultimodalEncoder for RemoteMultimodalEncoder {
    fn profile(&self) -> &MultimodalEmbeddingProfile {
        &self.profile
    }

    fn encode_documents(
        &self,
        units: &[MultimodalSourceUnit],
    ) -> Result<Vec<Vec<Vec<f64>>>, ProjectionIntegrityError> {
        let items: Result<Vec<_>, _> = units
            .iter()
            .map(|unit| item_payload(unit, self.resolver.as_deref()))
            .collect();
        let items = items?;
        self.request("document", items)
            .map(|sets| sets.into_iter().map(|set| set.vectors).collect())
            .map_err(|error| ProjectionIntegrityError(error.to_string()))
    }
}

impl RemoteMultimodalEncoder {
    pub fn encode_queries(
        &self,
        queries: &[String],
    ) -> Result<Vec<EmbeddingSet>, EmbeddingServiceError> {
        let items: Vec<Map<String, Value>> = queries
            .iter()
            .enumerate()
            .map(|(index, query)| {
                Map::from_iter([
                    ("item_id".to_string(), Value::from(format!("query-{index}"))),
                    ("modality".to_string(), Value::from("text")),
                    ("text".to_string(), Value::from(query.clone())),
                ])
            })
            .collect();
        self.request("query", items)
    }

    pub fn encode_image_queries(
        &self,
        images: &[Vec<u8>],
    ) -> Result<Vec<EmbeddingSet>, EmbeddingServiceError> {
        let items: Vec<Map<String, Value>> = images
            .iter()
            .enumerate()
            .map(|(index, image)| {
                let digest = sha256_hex(image);
                Map::from_iter([
                    (
                        "item_id".to_string(),
                        Value::from(format!("image-query-{index}")),
                    ),
                    ("modality".to_string(), Value::from("image")),
                    (
                        "asset".to_string(),
                        Value::Object(Map::from_iter([
                            ("content_type".to_string(), Value::from("image/png")),
                            ("sha256".to_string(), Value::from(digest)),
                            ("data_base64".to_string(), Value::from(base64_encode(image))),
                        ])),
                    ),
                ])
            })
            .collect();
        self.request("query", items)
    }
}
