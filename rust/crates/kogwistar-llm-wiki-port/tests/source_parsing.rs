use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::ingest::source_parsing::{
    build_parser_kwargs, model_from_env, parser_kwargs_value, provider_from_mode, ParserSignature,
};
use kogwistar_llm_wiki_port::models::IngestPipelineRequest;
use serde_json::{json, Value};

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

#[test]
fn parser_kwargs_match_python_contract() {
    let request = IngestPipelineRequest {
        workspace_id: "demo".to_owned(),
        source_uri: "file:///x.txt".to_owned(),
        title: "X".to_owned(),
        raw_text: "hello".to_owned(),
        source_format: "text".to_owned(),
        operation_mode: "parse_first".to_owned(),
        parser_mode: "heuristic".to_owned(),
        parser_lane: "page_index".to_owned(),
        promotion_mode: "pending".to_owned(),
        auto_accept_threshold: 0.95,
        llm_provider: None,
        llm_model: None,
        provenance_policy: kogwistar_llm_wiki_port::models::ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: Default::default(),
    };
    let kwargs =
        build_parser_kwargs(&request, "source:1", ParserSignature::default(), None, None).unwrap();
    assert_eq!(
        parser_kwargs_value(&kwargs),
        json!({
            "document_id": "source:1",
            "title": "X",
            "raw_text": "hello",
            "source_format": "text",
            "mode": "heuristic",
            "llm_provider": Value::Null,
            "model": Value::Null,
            "provider_settings": Value::Null,
            "trace_log": Value::Null
        })
    );

    let llm_request = IngestPipelineRequest {
        parser_mode: "openai".to_owned(),
        llm_provider: Some("openai".to_owned()),
        llm_model: Some("gpt-4o-mini".to_owned()),
        ..request
    };
    let kwargs = build_parser_kwargs(
        &llm_request,
        "source:1",
        ParserSignature {
            accepts_llm_provider: true,
            accepts_model: true,
            accepts_provider_settings: true,
            ..ParserSignature::default()
        },
        Some(json!({"provider": "openai"})),
        None,
    )
    .unwrap();
    assert_eq!(kwargs.llm_provider.as_deref(), Some("openai"));
    assert_eq!(kwargs.model.as_deref(), Some("gpt-4o-mini"));
    assert_eq!(
        kwargs.provider_settings,
        Some(json!({"provider": "openai"}))
    );

    let unsupported = build_parser_kwargs(
        &llm_request,
        "source:1",
        ParserSignature::default(),
        None,
        None,
    );
    assert!(unsupported.is_err());
}
