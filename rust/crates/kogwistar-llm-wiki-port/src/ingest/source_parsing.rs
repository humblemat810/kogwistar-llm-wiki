use std::collections::BTreeMap;

#[must_use]
pub fn provider_from_mode(mode: &str) -> Option<String> {
    match mode {
        "ollama" | "gemini" | "openai" | "azure" => Some(mode.to_owned()),
        "azure_openai" => Some("azure".to_owned()),
        _ => None,
    }
}

#[must_use]
pub fn model_from_env(provider: Option<&str>, env: &BTreeMap<String, String>) -> Option<String> {
    let first_non_empty = |keys: &[&str]| {
        keys.iter()
            .filter_map(|key| env.get(*key))
            .find(|value| !value.trim().is_empty())
            .cloned()
    };
    match provider {
        Some("ollama") => first_non_empty(&[
            "KOGWISTAR_PARSER_MODEL",
            "OLLAMA_MODEL",
            "KG_DOC_PARSER_MODEL",
        ]),
        Some("gemini") => first_non_empty(&[
            "KOGWISTAR_PARSER_MODEL",
            "GEMINI_MODEL",
            "KG_DOC_PARSER_MODEL",
        ]),
        Some("openai") | Some("azure") => first_non_empty(&[
            "KOGWISTAR_PARSER_MODEL",
            "OPENAI_MODEL",
            "KG_DOC_PARSER_MODEL",
        ]),
        _ => first_non_empty(&["KOGWISTAR_PARSER_MODEL", "KG_DOC_PARSER_MODEL"]),
    }
}
