use std::collections::BTreeMap;
use std::fmt;

use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ProviderEndpointConfig {
    pub provider: String,
    pub model: String,
    pub temperature: f64,
    pub max_retries: u32,
    pub max_output_tokens: Option<u64>,
    pub ollama_num_ctx: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub base_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key_env: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub location: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<String>,
    pub pdf_text_mode: String,
    pub ocr_language: String,
    pub timeout_seconds: f64,
    pub retry_backoff_seconds: f64,
    pub retry_backoff_max_seconds: f64,
    pub max_in_flight_calls: u32,
}

impl Default for ProviderEndpointConfig {
    fn default() -> Self {
        Self {
            provider: "gemini".to_owned(),
            model: "gemini-2.5-flash".to_owned(),
            temperature: 0.1,
            max_retries: 2,
            max_output_tokens: None,
            ollama_num_ctx: None,
            base_url: None,
            api_key_env: None,
            api_version: None,
            project: None,
            location: None,
            reasoning_effort: None,
            pdf_text_mode: "disabled".to_owned(),
            ocr_language: "en".to_owned(),
            timeout_seconds: 120.0,
            retry_backoff_seconds: 0.25,
            retry_backoff_max_seconds: 5.0,
            max_in_flight_calls: 1,
        }
    }
}

#[derive(Debug)]
pub enum ProviderConfigError {
    InvalidModelSuffix,
}

impl fmt::Display for ProviderConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidModelSuffix => write!(formatter, "model has no OpenAI env suffix"),
        }
    }
}

impl std::error::Error for ProviderConfigError {}

#[must_use]
pub fn normalize_provider_name(provider: Option<&str>) -> Option<String> {
    provider
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_ascii_lowercase)
        .map(|normalized| match normalized.as_str() {
            "azure_openai" => "azure".to_owned(),
            "llm_router" | "router" => "openai".to_owned(),
            value => value.to_owned(),
        })
}

pub fn openai_env_suffix_for_model(model: Option<&str>) -> Result<String, ProviderConfigError> {
    let Some(model) = model else {
        return Err(ProviderConfigError::InvalidModelSuffix);
    };
    let normalized = model.trim().to_ascii_lowercase().replace(' ', "");
    if normalized.is_empty() {
        return Err(ProviderConfigError::InvalidModelSuffix);
    }
    let compact: String = normalized
        .chars()
        .filter(|character| !matches!(character, '-' | '_' | '.'))
        .collect();
    let suffix = match compact.as_str() {
        "gpt4o" => "GPT4O",
        "gpt4omini" => "GPT4O_MINI",
        "gpt41" => "GPT4_1",
        "gpt41mini" => "GPT4_1_MINI",
        "gpt41nano" => "GPT4_1_NANO",
        "gpt5mini" => "GPT5_MINI",
        "gpt5nano" => "GPT5_NANO",
        "gpt5chat" => "GPT5_CHAT",
        _ if matches!(normalized.as_str(), "gpt-4.1" | "gpt4.1") => "GPT4_1",
        _ if matches!(normalized.as_str(), "gpt-4.1-mini" | "gpt4.1-mini") => "GPT4_1_MINI",
        _ if matches!(normalized.as_str(), "gpt-4.1-nano" | "gpt4.1-nano") => "GPT4_1_NANO",
        _ if matches!(normalized.as_str(), "gpt-5-mini" | "gpt5-mini") => "GPT5_MINI",
        _ if matches!(normalized.as_str(), "gpt-5-nano" | "gpt5-nano") => "GPT5_NANO",
        _ if matches!(normalized.as_str(), "gpt-5-chat" | "gpt5-chat") => "GPT5_CHAT",
        _ => return Err(ProviderConfigError::InvalidModelSuffix),
    };
    Ok(suffix.to_owned())
}

fn env_value(env: &BTreeMap<String, String>, names: &[&str]) -> Option<String> {
    names
        .iter()
        .filter_map(|name| env.get(*name))
        .find(|value| !value.trim().is_empty())
        .cloned()
}

pub fn build_parser_provider_endpoint_config(
    env: &BTreeMap<String, String>,
) -> ProviderEndpointConfig {
    let default = ProviderEndpointConfig::default();
    let provider = normalize_provider_name(
        env_value(
            env,
            &["KOGWISTAR_PARSER_PROVIDER", "KG_DOC_PARSER_PROVIDER"],
        )
        .as_deref(),
    )
    .unwrap_or_else(|| "ollama".to_owned());
    let model = env_value(
        env,
        &[
            "KOGWISTAR_PARSER_MODEL",
            "OLLAMA_MODEL",
            "GEMINI_MODEL",
            "KG_DOC_PARSER_MODEL",
        ],
    )
    .unwrap_or_else(|| "gemma4:e2b".to_owned());

    let mut config = ProviderEndpointConfig {
        provider,
        model,
        temperature: env_value(
            env,
            &["KOGWISTAR_PARSER_TEMPERATURE", "KG_DOC_PARSER_TEMPERATURE"],
        )
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(default.temperature),
        max_retries: env_value(
            env,
            &["KOGWISTAR_PARSER_MAX_RETRIES", "KG_DOC_PARSER_MAX_RETRIES"],
        )
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(default.max_retries),
        ..default
    };

    if config.provider == "openai" || config.provider == "azure" {
        if let Ok(suffix) = openai_env_suffix_for_model(Some(config.model.as_str())) {
            config.base_url = env_value(
                env,
                &[
                    &format!("OPENAI_DEPLOYMENT_ENDPOINT_{suffix}"),
                    &format!("OPENAI_ENDPOINT_{suffix}"),
                    &format!("AZURE_OPENAI_ENDPOINT_{suffix}"),
                    "KOGWISTAR_PARSER_BASE_URL",
                    "KG_DOC_PARSER_BASE_URL",
                ],
            );
            config.api_key_env = env_value(
                env,
                &[
                    &format!("OPENAI_API_KEY_{suffix}"),
                    &format!("AZURE_OPENAI_API_KEY_{suffix}"),
                    "KOGWISTAR_PARSER_API_KEY_ENV",
                    "KG_DOC_PARSER_API_KEY_ENV",
                ],
            );
            config.api_version = env_value(
                env,
                &[
                    &format!("OPENAI_DEPLOYMENT_VERSION_{suffix}"),
                    &format!("AZURE_OPENAI_API_VERSION_{suffix}"),
                    "OPENAI_API_VERSION",
                    "AZURE_OPENAI_API_VERSION",
                ],
            );
        } else {
            config.base_url = env_value(
                env,
                &[
                    "OPENAI_DEPLOYMENT_ENDPOINT",
                    "OPENAI_API_BASE",
                    "AZURE_OPENAI_ENDPOINT",
                    "OPENAI_ENDPOINT",
                    "KOGWISTAR_PARSER_BASE_URL",
                    "KG_DOC_PARSER_BASE_URL",
                ],
            );
            config.api_key_env = env_value(
                env,
                &[
                    "OPENAI_API_KEY",
                    "AZURE_OPENAI_API_KEY",
                    "KOGWISTAR_PARSER_API_KEY_ENV",
                    "KG_DOC_PARSER_API_KEY_ENV",
                ],
            );
            config.api_version =
                env_value(env, &["OPENAI_API_VERSION", "AZURE_OPENAI_API_VERSION"]);
        }
    } else {
        config.base_url = env_value(
            env,
            &["KOGWISTAR_PARSER_BASE_URL", "KG_DOC_PARSER_BASE_URL"],
        );
        config.api_key_env = env_value(
            env,
            &["KOGWISTAR_PARSER_API_KEY_ENV", "KG_DOC_PARSER_API_KEY_ENV"],
        );
    }
    config
}
