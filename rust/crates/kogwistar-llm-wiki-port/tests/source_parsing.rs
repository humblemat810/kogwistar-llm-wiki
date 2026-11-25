use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::ingest::source_parsing::{model_from_env, provider_from_mode};

fn env(value: &[(&str, &str)]) -> BTreeMap<String, String> {
    value
        .iter()
        .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
        .collect()
}

#[test]
fn provider_mode_matches_python_contract() {
    assert_eq!(provider_from_mode("ollama").as_deref(), Some("ollama"));
    assert_eq!(provider_from_mode("gemini").as_deref(), Some("gemini"));
    assert_eq!(provider_from_mode("openai").as_deref(), Some("openai"));
    assert_eq!(provider_from_mode("azure_openai").as_deref(), Some("azure"));
    assert_eq!(provider_from_mode("heuristic"), None);
}

#[test]
fn model_env_priority_matches_python_contract() {
    assert_eq!(
        model_from_env(
            Some("ollama"),
            &env(&[
                ("OLLAMA_MODEL", "ollama-model"),
                ("KOGWISTAR_PARSER_MODEL", "wiki-model")
            ])
        )
        .as_deref(),
        Some("wiki-model")
    );
    assert_eq!(
        model_from_env(Some("gemini"), &env(&[("GEMINI_MODEL", "gemini-model")])).as_deref(),
        Some("gemini-model")
    );
    assert_eq!(
        model_from_env(Some("openai"), &env(&[("OPENAI_MODEL", "openai-model")])).as_deref(),
        Some("openai-model")
    );
    assert_eq!(
        model_from_env(None, &env(&[("KG_DOC_PARSER_MODEL", "doc-model")])).as_deref(),
        Some("doc-model")
    );
    assert_eq!(model_from_env(Some("other"), &env(&[])), None);
}
