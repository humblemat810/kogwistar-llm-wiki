use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::providers::{
    build_parser_provider_endpoint_config, normalize_provider_name, openai_env_suffix_for_model,
    ProviderEndpointConfig,
};

fn env(value: &[(&str, &str)]) -> BTreeMap<String, String> {
    value
        .iter()
        .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
        .collect()
}

#[test]
fn provider_names_match_python_aliases() {
    assert_eq!(
        normalize_provider_name(Some("azure_openai")).as_deref(),
        Some("azure")
    );
    assert_eq!(
        normalize_provider_name(Some("azure")).as_deref(),
        Some("azure")
    );
    assert_eq!(
        normalize_provider_name(Some("Ollama")).as_deref(),
        Some("ollama")
    );
    assert_eq!(
        normalize_provider_name(Some("llm_router")).as_deref(),
        Some("openai")
    );
}

#[test]
fn openai_model_suffixes_match_python() {
    assert_eq!(
        openai_env_suffix_for_model(Some("gpt-4o-mini")).unwrap(),
        "GPT4O_MINI"
    );
    assert_eq!(
        openai_env_suffix_for_model(Some("gpt-5-chat")).unwrap(),
        "GPT5_CHAT"
    );
    assert!(openai_env_suffix_for_model(Some("gpt-5-experimental-chat")).is_err());
}

#[test]
fn parser_endpoint_config_prefers_kogwistar_env() {
    let config = build_parser_provider_endpoint_config(&env(&[
        ("KOGWISTAR_PARSER_PROVIDER", "azure_openai"),
        ("KOGWISTAR_PARSER_MODEL", "gpt-5-nano"),
        ("KOGWISTAR_PARSER_BASE_URL", "https://kogwistar.example/"),
        ("KOGWISTAR_PARSER_API_KEY_ENV", "KOGWISTAR_KEY"),
        ("OPENAI_DEPLOYMENT_VERSION_GPT5_NANO", "2024-10-21"),
        ("KG_DOC_PARSER_PROVIDER", "ollama"),
        ("KG_DOC_PARSER_MODEL", "legacy-model"),
        ("KG_DOC_PARSER_BASE_URL", "http://legacy.example/"),
    ]));

    assert_eq!(config.provider, "azure");
    assert_eq!(config.model, "gpt-5-nano");
    assert_eq!(
        config.base_url.as_deref(),
        Some("https://kogwistar.example/")
    );
    assert_eq!(config.api_key_env.as_deref(), Some("KOGWISTAR_KEY"));
    assert_eq!(config.api_version.as_deref(), Some("2024-10-21"));
    assert_eq!(config.temperature, 0.1);
    assert_eq!(config.max_retries, 2);
    assert_eq!(config.timeout_seconds, 120.0);
}

#[test]
fn parser_endpoint_defaults_match_python() {
    assert_eq!(
        build_parser_provider_endpoint_config(&BTreeMap::new()),
        ProviderEndpointConfig {
            provider: "ollama".to_owned(),
            model: "gemma4:e2b".to_owned(),
            base_url: None,
            api_key_env: None,
            ..ProviderEndpointConfig::default()
        }
    );
}
