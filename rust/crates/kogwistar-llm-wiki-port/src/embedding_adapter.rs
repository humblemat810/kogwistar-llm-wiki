/// Embedding provider resolution and profile contracts.
///
/// Mirrors Python contract `src/kogwistar_llm_wiki/embeddings/embedding_config_resolver.py`.
/// Resolves embedding providers per graph space and translates provider settings
/// into core embedding profiles.
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Embedding spaces shared across graph namespaces.
pub const EMBEDDING_SPACES: [&str; 4] = ["conversation", "workflow", "knowledge", "wisdom"];

/// Supported embedding providers.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EmbeddingProvider {
    Fake,
    OpenAI,
    Vertex,
    Ollama,
}

impl EmbeddingProvider {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Fake => "fake",
            Self::OpenAI => "openai",
            Self::Vertex => "vertex",
            Self::Ollama => "ollama",
        }
    }

    pub fn from_str_value(value: &str) -> Result<Self, EmbeddingError> {
        match value {
            "fake" => Ok(Self::Fake),
            "openai" => Ok(Self::OpenAI),
            "vertex" => Ok(Self::Vertex),
            "ollama" => Ok(Self::Ollama),
            _ => Err(EmbeddingError::UnsupportedProvider(value.to_owned())),
        }
    }
}

/// Deterministic in-memory fallback embedding function used by provider-free tests.
#[derive(Clone, Debug, Default)]
pub struct TinyEmbeddingFunction;

impl TinyEmbeddingFunction {
    #[must_use]
    pub const fn name(&self) -> &'static str {
        "kogwistar-llm-wiki-embedding-v1"
    }

    /// Produce deterministic 2-d vectors from text inputs.
    #[must_use]
    pub fn call(&self, input: &[String]) -> Vec<Vec<f64>> {
        input
            .iter()
            .map(|value| {
                let text = value.as_str();
                let checksum: f64 = (text.chars().map(|ch| ch as u32).sum::<u32>() % 97 + 1) as f64;
                vec![(text.len() + 1) as f64, checksum]
            })
            .collect()
    }
}

/// Embedding function trait implemented by concrete providers.
pub trait EmbeddingFunctionLike: Send + Sync {
    fn name(&self) -> String;
    fn call(&self, input: &[String]) -> Vec<Vec<f64>>;
    fn clone_box(&self) -> Box<dyn EmbeddingFunctionLike>;
}

impl EmbeddingFunctionLike for TinyEmbeddingFunction {
    fn name(&self) -> String {
        self.name().to_owned()
    }

    fn call(&self, input: &[String]) -> Vec<Vec<f64>> {
        self.call(input)
    }

    fn clone_box(&self) -> Box<dyn EmbeddingFunctionLike> {
        Box::new(self.clone())
    }
}

/// Provider configuration resolved from environment or explicit arguments.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EmbeddingProviderConfig {
    pub provider: EmbeddingProvider,
    pub model: String,
    pub dimension: Option<usize>,
    pub base_url: Option<String>,
    pub api_key_env: Option<String>,
    pub max_sequence_length: Option<usize>,
    pub crop_token_budget: Option<usize>,
    pub tokenizer_fingerprint: Option<String>,
}

impl Default for EmbeddingProviderConfig {
    fn default() -> Self {
        Self {
            provider: EmbeddingProvider::Fake,
            model: "kogwistar-llm-wiki-embedding-v1".to_owned(),
            dimension: None,
            base_url: None,
            api_key_env: None,
            max_sequence_length: None,
            crop_token_budget: None,
            tokenizer_fingerprint: None,
        }
    }
}

/// Core embedding profile translated from provider config.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EmbeddingProfile {
    pub provider: EmbeddingProvider,
    pub model: String,
    pub dimension: Option<usize>,
    pub similarity_metric: String,
    pub endpoint_fingerprint: Option<String>,
    pub max_sequence_length: Option<usize>,
    pub crop_token_budget: Option<usize>,
    pub tokenizer_fingerprint: Option<String>,
    pub crop_policy: Option<String>,
}

impl EmbeddingProfile {
    #[must_use]
    pub fn from_config(config: &EmbeddingProviderConfig) -> Self {
        let endpoint_fingerprint = config.base_url.as_ref().map(|url| {
            use std::collections::hash_map::DefaultHasher;
            use std::hash::{Hash, Hasher};
            let mut hasher = DefaultHasher::new();
            url.hash(&mut hasher);
            format!("{:x}", hasher.finish())
        });
        Self {
            provider: config.provider,
            model: config.model.clone(),
            dimension: config.dimension,
            similarity_metric: "cosine".to_owned(),
            endpoint_fingerprint,
            max_sequence_length: config.max_sequence_length,
            crop_token_budget: config.crop_token_budget,
            tokenizer_fingerprint: config.tokenizer_fingerprint.clone(),
            crop_policy: config.crop_token_budget.map(|_| "token_prefix".to_owned()),
        }
    }
}

/// Error raised during embedding resolution.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EmbeddingError {
    UnsupportedProvider(String),
    ModelRequired,
    DimensionRequired(String),
    FunctionAndConfigBoth,
}

impl std::fmt::Display for EmbeddingError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnsupportedProvider(v) => write!(f, "unsupported embedding provider {v:?}"),
            Self::ModelRequired => write!(f, "embedding model is required"),
            Self::DimensionRequired(space) => {
                write!(
                    f,
                    "embedding dimension is required for the real {space} provider"
                )
            }
            Self::FunctionAndConfigBoth => {
                write!(
                    f,
                    "pass either embedding_function or embedding_config, not both"
                )
            }
        }
    }
}

impl std::error::Error for EmbeddingError {}

fn env_any_set(keys: &[&str]) -> bool {
    keys.iter()
        .any(|key| std::env::var(key).map(|v| !v.is_empty()).unwrap_or(false))
}

/// Resolve one embedding provider without creating a second factory.
#[allow(clippy::too_many_arguments)]
pub fn resolve_embedding_function(
    _namespace: &str,
    embedding_function: Option<&dyn EmbeddingFunctionLike>,
    embedding_config: Option<&EmbeddingProviderConfig>,
    embedding_provider: Option<&str>,
    embedding_model: Option<&str>,
    embedding_dimension: Option<usize>,
    embedding_base_url: Option<&str>,
    embedding_api_key_env: Option<&str>,
    embedding_max_sequence_length: Option<usize>,
    embedding_crop_token_budget: Option<usize>,
    embedding_tokenizer_fingerprint: Option<&str>,
) -> Result<(Box<dyn EmbeddingFunctionLike>, EmbeddingProviderConfig), EmbeddingError> {
    if embedding_function.is_some() && embedding_config.is_some() {
        return Err(EmbeddingError::FunctionAndConfigBoth);
    }

    let configured = embedding_provider.is_some()
        || embedding_model.is_some()
        || embedding_dimension.is_some()
        || embedding_base_url.is_some()
        || embedding_api_key_env.is_some()
        || embedding_max_sequence_length.is_some()
        || embedding_crop_token_budget.is_some()
        || embedding_tokenizer_fingerprint.is_some()
        || env_any_set(&[
            "KOGWISTAR_LLM_WIKI_EMBED_PROVIDER",
            "KOGWISTAR_LLM_WIKI_EMBED_MODEL",
            "KOGWISTAR_LLM_WIKI_EMBED_DIMENSION",
            "KOGWISTAR_LLM_WIKI_EMBED_BASE_URL",
            "KOGWISTAR_LLM_WIKI_EMBED_API_KEY_ENV",
            "KOGWISTAR_LLM_WIKI_EMBED_MAX_SEQUENCE_LENGTH",
            "KOGWISTAR_LLM_WIKI_EMBED_CROP_TOKEN_BUDGET",
            "KOGWISTAR_LLM_WIKI_EMBED_TOKENIZER_FINGERPRINT",
        ]);

    let (function, config) = if let Some(f) = embedding_function {
        let config = EmbeddingProviderConfig {
            provider: embedding_provider
                .and_then(|v| EmbeddingProvider::from_str_value(v).ok())
                .unwrap_or(EmbeddingProvider::Fake),
            model: embedding_model
                .map(|v| v.to_owned())
                .unwrap_or_else(|| f.name()),
            dimension: embedding_dimension,
            base_url: embedding_base_url.map(|v| v.to_owned()),
            api_key_env: embedding_api_key_env.map(|v| v.to_owned()),
            max_sequence_length: embedding_max_sequence_length,
            crop_token_budget: embedding_crop_token_budget,
            tokenizer_fingerprint: embedding_tokenizer_fingerprint.map(|v| v.to_owned()),
        };
        (f.clone_box(), config)
    } else if let Some(c) = embedding_config {
        (
            Box::new(TinyEmbeddingFunction) as Box<dyn EmbeddingFunctionLike>,
            c.clone(),
        )
    } else if configured {
        let provider = embedding_provider
            .and_then(|v| EmbeddingProvider::from_str_value(v).ok())
            .unwrap_or(EmbeddingProvider::Fake);
        let model = embedding_model
            .map(|v| v.to_owned())
            .unwrap_or_else(|| "kogwistar-llm-wiki-embedding-v1".to_owned());
        let config = EmbeddingProviderConfig {
            provider,
            model,
            dimension: embedding_dimension,
            base_url: embedding_base_url.map(|v| v.to_owned()),
            api_key_env: embedding_api_key_env.map(|v| v.to_owned()),
            max_sequence_length: embedding_max_sequence_length,
            crop_token_budget: embedding_crop_token_budget,
            tokenizer_fingerprint: embedding_tokenizer_fingerprint.map(|v| v.to_owned()),
        };
        (
            Box::new(TinyEmbeddingFunction) as Box<dyn EmbeddingFunctionLike>,
            config,
        )
    } else {
        let f = TinyEmbeddingFunction;
        let config = EmbeddingProviderConfig::default();
        (Box::new(f) as Box<dyn EmbeddingFunctionLike>, config)
    };

    Ok((function, config))
}

type EmbeddingFunctionMap = HashMap<String, Box<dyn EmbeddingFunctionLike>>;
type EmbeddingProviderConfigMap = HashMap<String, EmbeddingProviderConfig>;

/// Resolve embedding functions for all graph spaces.
#[allow(clippy::too_many_arguments)]
pub fn resolve_embedding_functions(
    _namespace: &str,
    embedding_functions: Option<&HashMap<String, Box<dyn EmbeddingFunctionLike>>>,
    embedding_configs: Option<&HashMap<String, EmbeddingProviderConfig>>,
    embedding_provider: Option<&str>,
    embedding_model: Option<&str>,
    embedding_dimension: Option<usize>,
    embedding_base_url: Option<&str>,
    embedding_api_key_env: Option<&str>,
    embedding_max_sequence_length: Option<usize>,
    embedding_crop_token_budget: Option<usize>,
    embedding_tokenizer_fingerprint: Option<&str>,
) -> Result<(EmbeddingFunctionMap, EmbeddingProviderConfigMap), EmbeddingError> {
    let mut functions: EmbeddingFunctionMap = HashMap::new();
    let mut configs: EmbeddingProviderConfigMap = HashMap::new();

    let shared = resolve_embedding_function(
        _namespace,
        embedding_functions.and_then(|m| m.get("global").map(|b| b.as_ref())),
        embedding_configs.and_then(|m| m.get("global")),
        embedding_provider,
        embedding_model,
        embedding_dimension,
        embedding_base_url,
        embedding_api_key_env,
        embedding_max_sequence_length,
        embedding_crop_token_budget,
        embedding_tokenizer_fingerprint,
    )?;

    for space in EMBEDDING_SPACES {
        let space_func = embedding_functions.and_then(|m| m.get(space));
        let space_cfg = embedding_configs.and_then(|m| m.get(space));
        if space_func.is_none() && space_cfg.is_none() {
            functions.insert(space.to_owned(), shared.0.clone_box());
            configs.insert(space.to_owned(), shared.1.clone());
            continue;
        }
        let (resolved_function, resolved_config) = resolve_embedding_function(
            space,
            space_func.map(|b| b.as_ref()),
            space_cfg,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )?;
        functions.insert(space.to_owned(), resolved_function);
        configs.insert(space.to_owned(), resolved_config);
    }

    // Validate that real providers have declared dimensions for shared pgvector tables.
    for (space, config) in &configs {
        if config.provider != EmbeddingProvider::Fake {
            let has_declared_dimension = config.dimension.is_some()
                || embedding_configs
                    .map(|m| m.contains_key(space.as_str()))
                    .unwrap_or(false)
                || embedding_dimension.is_some()
                || env_any_set(&[
                    "KOGWISTAR_LLM_WIKI_EMBED_DIMENSION",
                    "KOGWISTAR_EMBED_DIMENSION",
                    "KG_DOC_EMBED_DIMENSION",
                ]);
            if !has_declared_dimension {
                return Err(EmbeddingError::DimensionRequired(space.to_owned()));
            }
        }
    }

    Ok((functions, configs))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tiny_embedding_function_deterministic() {
        let f = TinyEmbeddingFunction;
        let vectors = f.call(&["hello".to_owned(), "world".to_owned()]);
        assert_eq!(vectors.len(), 2);
        assert_eq!(vectors[0].len(), 2);
        assert_eq!(vectors[0], f.call(&["hello".to_owned()])[0]);
        assert_eq!(f.name(), "kogwistar-llm-wiki-embedding-v1");
    }

    #[test]
    fn embedding_profile_from_config() {
        let config = EmbeddingProviderConfig {
            provider: EmbeddingProvider::OpenAI,
            model: "text-embedding-3-small".to_owned(),
            dimension: Some(1536),
            base_url: Some("https://api.openai.com/v1".to_owned()),
            api_key_env: Some("OPENAI_API_KEY".to_owned()),
            max_sequence_length: Some(8192),
            crop_token_budget: Some(2048),
            tokenizer_fingerprint: Some("cl100k_base".to_owned()),
        };
        let profile = EmbeddingProfile::from_config(&config);
        assert_eq!(profile.provider, EmbeddingProvider::OpenAI);
        assert_eq!(profile.model, "text-embedding-3-small");
        assert_eq!(profile.dimension, Some(1536));
        assert_eq!(profile.similarity_metric, "cosine");
        assert_eq!(profile.crop_policy, Some("token_prefix".to_owned()));
        assert!(profile.endpoint_fingerprint.is_some());
    }

    #[test]
    fn resolve_embedding_function_defaults_to_fake() {
        let (function, config) = resolve_embedding_function(
            "global", None, None, None, None, None, None, None, None, None, None,
        )
        .unwrap();
        assert_eq!(config.provider, EmbeddingProvider::Fake);
        assert_eq!(function.name(), "kogwistar-llm-wiki-embedding-v1");
        let vectors = function.call(&["test".to_owned()]);
        assert_eq!(vectors.len(), 1);
        assert_eq!(vectors[0].len(), 2);
    }

    #[test]
    fn resolve_embedding_function_rejects_both_function_and_config() {
        let config = EmbeddingProviderConfig::default();
        let f = TinyEmbeddingFunction;
        let err = resolve_embedding_function(
            "global",
            Some(&f),
            Some(&config),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        );
        assert!(matches!(err, Err(EmbeddingError::FunctionAndConfigBoth)));
    }

    #[test]
    fn resolve_embedding_functions_all_spaces_share_by_default() {
        let (functions, configs) = resolve_embedding_functions(
            "global", None, None, None, None, None, None, None, None, None, None,
        )
        .unwrap();
        assert_eq!(functions.len(), 4);
        assert_eq!(configs.len(), 4);
        for space in EMBEDDING_SPACES {
            assert!(functions.contains_key(space));
            assert!(configs.contains_key(space));
            assert_eq!(configs[space].provider, EmbeddingProvider::Fake);
        }
    }

    #[test]
    fn embedding_provider_from_str() {
        assert_eq!(
            EmbeddingProvider::from_str_value("openai").unwrap(),
            EmbeddingProvider::OpenAI
        );
        assert_eq!(
            EmbeddingProvider::from_str_value("fake").unwrap(),
            EmbeddingProvider::Fake
        );
        assert!(EmbeddingProvider::from_str_value("unknown").is_err());
    }
}
