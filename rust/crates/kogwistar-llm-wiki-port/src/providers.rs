use std::collections::BTreeMap;
use std::fmt;

use serde::Serialize;
use serde_json::{json, Value};

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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fallback_specs: Vec<ProviderEndpointConfig>,
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
            fallback_specs: Vec::new(),
        }
    }
}

#[derive(Debug)]
pub enum ProviderConfigError {
    InvalidModelSuffix,
    InvalidProviderChain,
    DuplicateProviderChain,
}

impl fmt::Display for ProviderConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidModelSuffix => write!(formatter, "model has no OpenAI env suffix"),
            Self::InvalidProviderChain => {
                write!(
                    formatter,
                    "KOGWISTAR_MAINTENANCE_PROVIDER_CHAIN contains an invalid provider"
                )
            }
            Self::DuplicateProviderChain => {
                write!(
                    formatter,
                    "KOGWISTAR_MAINTENANCE_PROVIDER_CHAIN must not contain duplicates"
                )
            }
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

#[must_use]
pub fn build_maintenance_provider_endpoint_config(
    env: &BTreeMap<String, String>,
) -> ProviderEndpointConfig {
    let default = ProviderEndpointConfig {
        provider: "ollama".to_owned(),
        model: "gemma4:e2b".to_owned(),
        ..ProviderEndpointConfig::default()
    };
    let provider = normalize_provider_name(
        env_value(
            env,
            &[
                "KOGWISTAR_MAINTENANCE_PROVIDER",
                "KOGWISTAR_PARSER_PROVIDER",
                "KG_DOC_PARSER_PROVIDER",
            ],
        )
        .as_deref(),
    )
    .unwrap_or_else(|| "ollama".to_owned());
    let model = env_value(
        env,
        &[
            "KOGWISTAR_MAINTENANCE_MODEL",
            "KOGWISTAR_PARSER_MODEL",
            "KG_DOC_PARSER_MODEL",
            "OLLAMA_MODEL",
            "GEMINI_MODEL",
        ],
    )
    .unwrap_or_else(|| "gemma4:e2b".to_owned());
    ProviderEndpointConfig {
        provider,
        model,
        temperature: env_value(
            env,
            &[
                "KOGWISTAR_MAINTENANCE_TEMPERATURE",
                "KOGWISTAR_PARSER_TEMPERATURE",
                "KG_DOC_PARSER_TEMPERATURE",
            ],
        )
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(default.temperature),
        max_retries: env_value(
            env,
            &[
                "KOGWISTAR_MAINTENANCE_MAX_RETRIES",
                "KOGWISTAR_PARSER_MAX_RETRIES",
                "KG_DOC_PARSER_MAX_RETRIES",
            ],
        )
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(default.max_retries),
        base_url: env_value(
            env,
            &[
                "KOGWISTAR_MAINTENANCE_BASE_URL",
                "KOGWISTAR_PARSER_BASE_URL",
                "KG_DOC_PARSER_BASE_URL",
            ],
        ),
        api_key_env: env_value(
            env,
            &[
                "KOGWISTAR_MAINTENANCE_API_KEY_ENV",
                "KOGWISTAR_PARSER_API_KEY_ENV",
                "KG_DOC_PARSER_API_KEY_ENV",
            ],
        ),
        ..default
    }
}

fn provider_specific_chain_config(
    provider: &str,
    primary: &ProviderEndpointConfig,
    env: &BTreeMap<String, String>,
) -> ProviderEndpointConfig {
    let prefix = format!("KOGWISTAR_MAINTENANCE_{}", provider.to_ascii_uppercase());
    let default_model = if provider == "ollama" {
        "gemma4:e2b".to_owned()
    } else {
        primary.model.clone()
    };
    let default_base_url = if provider == "ollama" {
        Some("http://host.docker.internal:11434".to_owned())
    } else if provider == "codex" {
        Some("http://host.docker.internal:8791".to_owned())
    } else {
        primary.base_url.clone()
    };
    let default_key_env = if provider == "ollama" {
        None
    } else if provider == "codex" {
        Some("LLM_WIKI_CODEX_BRIDGE_TOKEN".to_owned())
    } else {
        primary.api_key_env.clone()
    };
    ProviderEndpointConfig {
        provider: provider.to_owned(),
        model: env_value(env, &[&format!("{prefix}_MODEL")]).unwrap_or(default_model),
        temperature: env_value(env, &[&format!("{prefix}_TEMPERATURE")])
            .and_then(|value| value.trim().parse().ok())
            .unwrap_or(primary.temperature),
        base_url: env_value(env, &[&format!("{prefix}_BASE_URL")]).or(default_base_url),
        api_key_env: env_value(env, &[&format!("{prefix}_API_KEY_ENV")]).or(default_key_env),
        max_retries: env_value(env, &[&format!("{prefix}_MAX_RETRIES")])
            .and_then(|value| value.trim().parse().ok())
            .unwrap_or(primary.max_retries),
        ..ProviderEndpointConfig::default()
    }
}

pub fn resolve_maintenance_provider_settings(
    env: &BTreeMap<String, String>,
    include_provider_chain: bool,
) -> Result<WorkflowProviderSettings, ProviderConfigError> {
    let primary = build_maintenance_provider_endpoint_config(env);
    if !include_provider_chain {
        return Ok(WorkflowProviderSettings {
            proposal_mode: None,
            parser: primary,
        });
    }
    let Some(chain_value) = env.get("KOGWISTAR_MAINTENANCE_PROVIDER_CHAIN") else {
        return Ok(WorkflowProviderSettings {
            proposal_mode: None,
            parser: primary,
        });
    };
    let names = chain_value
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| {
            normalize_provider_name(Some(value)).ok_or(ProviderConfigError::InvalidProviderChain)
        })
        .collect::<Result<Vec<_>, _>>()?;
    if names.is_empty() {
        return Err(ProviderConfigError::InvalidProviderChain);
    }
    let unique = names
        .iter()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    if unique.len() != names.len() {
        return Err(ProviderConfigError::DuplicateProviderChain);
    }
    let mut primary = provider_specific_chain_config(&names[0], &primary, env);
    let fallback_specs = names[1..]
        .iter()
        .map(|provider| provider_specific_chain_config(provider, &primary, env))
        .collect::<Vec<_>>();
    primary.fallback_specs = fallback_specs;
    Ok(WorkflowProviderSettings {
        proposal_mode: None,
        parser: primary,
    })
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct WorkflowProviderSettings {
    pub proposal_mode: Option<String>,
    pub parser: ProviderEndpointConfig,
}

pub fn resolve_parser_provider_settings(
    env: &BTreeMap<String, String>,
) -> WorkflowProviderSettings {
    WorkflowProviderSettings {
        proposal_mode: env_value(
            env,
            &[
                "KOGWISTAR_PARSER_PROPOSAL_MODE",
                "KG_DOC_PARSER_PROPOSAL_MODE",
            ],
        )
        .or_else(|| Some("children".to_owned())),
        parser: build_parser_provider_endpoint_config(env),
    }
}

#[must_use]
pub fn provider_config_summary(settings: &WorkflowProviderSettings) -> Value {
    json!({
        "proposal_mode": settings.proposal_mode,
        "provider": settings.parser.provider,
        "provider_chain": [
            settings.parser.provider.clone(),
            settings.parser.fallback_specs.iter().map(|spec| spec.provider.clone()).collect::<Vec<_>>()
        ],
        "model": settings.parser.model,
        "temperature": settings.parser.temperature,
        "base_url": settings.parser.base_url,
        "api_key_env": settings.parser.api_key_env,
        "api_version": settings.parser.api_version,
        "project": settings.parser.project,
        "location": settings.parser.location,
        "max_retries": settings.parser.max_retries,
    })
}
