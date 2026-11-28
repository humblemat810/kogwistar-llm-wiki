// T5.13 compose TUI contracts - mirroring Python codex/codex_compose_tui.py
use serde::{Deserialize, Serialize};

/// Mirrors Python `LaunchMode`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LaunchMode {
    Standalone,
    Memory,
    Host,
    HostMemory,
}

/// Mirrors Python `StackMode`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum StackMode {
    #[default]
    Split,
    Combined,
}

/// Mirrors Python `EmbeddingMode`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum EmbeddingMode {
    #[default]
    None,
    Vllm,
    Transformers,
}

/// Mirrors Python `LaunchStep`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LaunchStep {
    pub description: String,
    pub command: Vec<String>,
    pub background: bool,
    pub environment: Option<Vec<(String, String)>>,
}

/// Mirrors Python `TuiConfiguration` (non-secret deployment settings).
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
pub struct TuiConfiguration {
    pub stack: StackMode,
    pub embedding: EmbeddingMode,
    pub maintenance_enabled: bool,
    pub request_enabled: bool,
    pub background_enabled: bool,
    pub maintenance_profile: String,
    pub profile_ladder_json: Option<String>,
    pub combined_memory_limit: String,
    pub combined_cpu_limit: String,
    pub embedding_dimension: i64,
    pub embedding_max_model_len: i64,
    pub embedding_crop_token_budget: i64,
    pub env_file: String,
}

/// Mirrors Python `TuiConfiguration.__post_init__` validation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ComposeTuiError {
    InvalidMaintenanceProfile,
    InvalidEmbeddingDimension,
    InvalidEmbeddingMaxModelLen,
    InvalidEmbeddingCropTokenBudget,
    InvalidProfileLadderJson,
}

impl std::fmt::Display for ComposeTuiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidMaintenanceProfile => {
                write!(
                    f,
                    "maintenance profile must be high, balanced, budgeted, or lite"
                )
            }
            Self::InvalidEmbeddingDimension => write!(f, "embedding dimension must be positive"),
            Self::InvalidEmbeddingMaxModelLen => {
                write!(f, "embedding max model length must be positive")
            }
            Self::InvalidEmbeddingCropTokenBudget => {
                write!(
                    f,
                    "embedding crop token budget must be between 1 and max model length"
                )
            }
            Self::InvalidProfileLadderJson => write!(f, "profile ladder must be valid JSON"),
        }
    }
}

impl std::error::Error for ComposeTuiError {}

impl TuiConfiguration {
    pub fn default_config() -> Self {
        Self {
            stack: StackMode::Split,
            embedding: EmbeddingMode::None,
            maintenance_enabled: false,
            request_enabled: true,
            background_enabled: false,
            maintenance_profile: "balanced".to_owned(),
            profile_ladder_json: None,
            combined_memory_limit: "512m".to_owned(),
            combined_cpu_limit: "0.25".to_owned(),
            embedding_dimension: 1024,
            embedding_max_model_len: 8192,
            embedding_crop_token_budget: 7680,
            env_file: ".env".to_owned(),
        }
    }

    pub fn validate(&self) -> Result<(), ComposeTuiError> {
        let valid_profiles = ["high", "balanced", "budgeted", "lite"];
        if !valid_profiles.contains(&self.maintenance_profile.as_str()) {
            return Err(ComposeTuiError::InvalidMaintenanceProfile);
        }
        if self.embedding_dimension < 1 {
            return Err(ComposeTuiError::InvalidEmbeddingDimension);
        }
        if self.embedding_max_model_len < 1 {
            return Err(ComposeTuiError::InvalidEmbeddingMaxModelLen);
        }
        if !(1..=self.embedding_max_model_len).contains(&self.embedding_crop_token_budget) {
            return Err(ComposeTuiError::InvalidEmbeddingCropTokenBudget);
        }
        if let Some(ladder) = &self.profile_ladder_json {
            if !ladder.is_empty() {
                serde_json::from_str::<serde_json::Value>(ladder)
                    .map_err(|_| ComposeTuiError::InvalidProfileLadderJson)?;
            }
        }
        Ok(())
    }

    pub fn configuration_environment(&self) -> Vec<(String, String)> {
        let mut values = vec![
            (
                "LLM_WIKI_MAINTENANCE_ENABLED".to_owned(),
                self.maintenance_enabled.to_string(),
            ),
            (
                "LLM_WIKI_MAINTENANCE_REQUEST_ENABLED".to_owned(),
                self.request_enabled.to_string(),
            ),
            (
                "LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED".to_owned(),
                self.background_enabled.to_string(),
            ),
            (
                "LLM_WIKI_MAINTENANCE_PROFILE".to_owned(),
                self.maintenance_profile.clone(),
            ),
            (
                "LLM_WIKI_COMBINED_MEMORY_LIMIT".to_owned(),
                self.combined_memory_limit.clone(),
            ),
            (
                "LLM_WIKI_COMBINED_CPU_LIMIT".to_owned(),
                self.combined_cpu_limit.clone(),
            ),
            (
                "LLM_WIKI_EMBEDDING_DIMENSION".to_owned(),
                self.embedding_dimension.to_string(),
            ),
            (
                "LLM_WIKI_EMBEDDING_MAX_MODEL_LEN".to_owned(),
                self.embedding_max_model_len.to_string(),
            ),
            (
                "LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET".to_owned(),
                self.embedding_crop_token_budget.to_string(),
            ),
        ];
        if let Some(ladder) = &self.profile_ladder_json {
            values.push((
                "LLM_WIKI_MAINTENANCE_PROFILE_LADDER".to_owned(),
                ladder.clone(),
            ));
        }
        values
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_validation_passes() {
        let config = TuiConfiguration::default_config();
        assert!(config.validate().is_ok());
    }

    #[test]
    fn invalid_maintenance_profile_rejected() {
        let mut config = TuiConfiguration::default_config();
        config.maintenance_profile = "invalid".to_owned();
        assert_eq!(
            config.validate().unwrap_err(),
            ComposeTuiError::InvalidMaintenanceProfile
        );
    }

    #[test]
    fn invalid_crop_budget_rejected() {
        let mut config = TuiConfiguration::default_config();
        config.embedding_crop_token_budget = 0;
        assert_eq!(
            config.validate().unwrap_err(),
            ComposeTuiError::InvalidEmbeddingCropTokenBudget
        );
    }

    #[test]
    fn configuration_environment_allowlisted_keys() {
        let config = TuiConfiguration::default_config();
        let env = config.configuration_environment();
        let keys: Vec<&str> = env.iter().map(|(k, _)| k.as_str()).collect();
        assert!(keys.contains(&"LLM_WIKI_MAINTENANCE_PROFILE"));
        assert!(keys.contains(&"LLM_WIKI_EMBEDDING_DIMENSION"));
        assert!(!keys.contains(&"LLM_WIKI_MAINTENANCE_PROFILE_LADDER"));
    }

    #[test]
    fn profile_ladder_json_invalid_rejected() {
        let mut config = TuiConfiguration::default_config();
        config.profile_ladder_json = Some("{not json".to_owned());
        assert_eq!(
            config.validate().unwrap_err(),
            ComposeTuiError::InvalidProfileLadderJson
        );
    }

    #[test]
    fn launch_modes_serialize_snake_case() {
        assert_eq!(
            serde_json::to_value(LaunchMode::HostMemory).unwrap(),
            serde_json::Value::String("host_memory".to_owned())
        );
    }
}
