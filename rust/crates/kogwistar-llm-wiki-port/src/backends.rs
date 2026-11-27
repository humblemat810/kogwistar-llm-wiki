//! Selection and lazy-construction contracts for optional vector backends.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
pub const SUPPORTED_BACKENDS: [&str; 4] = ["chroma", "postgres", "pinecone", "qdrant"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VectorBackendSettings {
    pub name: String,
    pub dimension: u32,
    pub prefix: String,
    pub pinecone_index_host: Option<String>,
    pub qdrant_url: Option<String>,
    pub qdrant_path: Option<String>,
}

impl VectorBackendSettings {
    pub fn new(name: &str, dimension: u32) -> Self {
        Self {
            name: name.to_string(),
            dimension,
            prefix: "kogwistar".to_string(),
            pinecone_index_host: None,
            qdrant_url: None,
            qdrant_path: None,
        }
    }

    pub fn with_pinecone_index_host(mut self, host: &str) -> Self {
        self.pinecone_index_host = Some(host.to_string());
        self
    }

    pub fn with_qdrant_url(mut self, url: &str) -> Self {
        self.qdrant_url = Some(url.to_string());
        self
    }

    pub fn with_qdrant_path(mut self, path: &str) -> Self {
        self.qdrant_path = Some(path.to_string());
        self
    }
}

pub fn from_env_vars<I, K, V>(name: &str, env: I, dimension: u32) -> VectorBackendSettings
where
    I: IntoIterator<Item = (K, V)>,
    K: AsRef<str>,
    V: AsRef<str>,
{
    let env: HashMap<String, String> = env
        .into_iter()
        .map(|(key, value)| (key.as_ref().to_string(), value.as_ref().to_string()))
        .collect();
    let get = |key: &str| env.get(key).cloned();
    VectorBackendSettings {
        name: name.to_string(),
        dimension,
        prefix: get("KOGWISTAR_VECTOR_PREFIX").unwrap_or_else(|| "kogwistar".to_string()),
        pinecone_index_host: get("PINECONE_INDEX_HOST"),
        qdrant_url: get("QDRANT_URL"),
        qdrant_path: get("QDRANT_PATH"),
    }
}

pub fn from_env(name: &str, dimension: u32) -> VectorBackendSettings {
    from_env_vars(name, std::env::vars(), dimension)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendFactory {
    Pinecone(VectorBackendSettings),
    Qdrant(VectorBackendSettings),
}

#[derive(Debug)]
pub enum BackendRegistryError {
    UnconfiguredQdrant,
    UnsupportedBackend(String),
}

impl std::fmt::Display for BackendRegistryError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnconfiguredQdrant => write!(
                formatter,
                "Qdrant backend requires QDRANT_URL or QDRANT_PATH; refusing an unconfigured in-memory store"
            ),
            Self::UnsupportedBackend(name) => write!(
                formatter,
                "Unsupported vector backend '{name}'; choose one of {}",
                SUPPORTED_BACKENDS.join(", ")
            ),
        }
    }
}

impl std::error::Error for BackendRegistryError {}

pub fn build_backend_factory(
    settings: VectorBackendSettings,
) -> Result<Option<BackendFactory>, BackendRegistryError> {
    match settings.name.as_str() {
        "chroma" | "postgres" => Ok(None),
        "pinecone" => Ok(Some(BackendFactory::Pinecone(settings))),
        "qdrant" => {
            if settings.qdrant_url.is_none() && settings.qdrant_path.is_none() {
                return Err(BackendRegistryError::UnconfiguredQdrant);
            }
            Ok(Some(BackendFactory::Qdrant(settings)))
        }
        name => Err(BackendRegistryError::UnsupportedBackend(name.to_string())),
    }
}
