use std::collections::BTreeMap;

use serde_json::{json, Value};

use crate::models::IngestPipelineRequest;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ParserSignature {
    pub accepts_document_id: bool,
    pub accepts_title: bool,
    pub accepts_raw_text: bool,
    pub accepts_source_format: bool,
    pub accepts_mode: bool,
    pub accepts_llm_provider: bool,
    pub accepts_model: bool,
    pub accepts_provider_settings: bool,
    pub accepts_trace_log: bool,
    pub supports_kwargs: bool,
}

impl Default for ParserSignature {
    fn default() -> Self {
        Self {
            accepts_document_id: true,
            accepts_title: true,
            accepts_raw_text: true,
            accepts_source_format: true,
            accepts_mode: true,
            accepts_llm_provider: false,
            accepts_model: false,
            accepts_provider_settings: false,
            accepts_trace_log: false,
            supports_kwargs: false,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ParserKwargs {
    pub document_id: String,
    pub title: String,
    pub raw_text: String,
    pub source_format: String,
    pub mode: String,
    pub llm_provider: Option<String>,
    pub model: Option<String>,
    pub provider_settings: Option<Value>,
    pub trace_log: Option<()>,
}

pub fn build_parser_kwargs(
    request: &IngestPipelineRequest,
    source_document_id: &str,
    signature: ParserSignature,
    provider_settings: Option<Value>,
    trace_log: Option<()>,
) -> Result<ParserKwargs, String> {
    let mut kwargs = ParserKwargs {
        document_id: source_document_id.to_owned(),
        title: request.title.clone(),
        raw_text: request.raw_text.clone(),
        source_format: request.source_format.clone(),
        mode: request.parser_mode.clone(),
        llm_provider: None,
        model: None,
        provider_settings: None,
        trace_log: None,
    };
    if request.parser_mode == "heuristic" {
        return Ok(kwargs);
    }

    let provider = request
        .llm_provider
        .clone()
        .or_else(|| provider_from_mode(request.parser_mode.as_str()));
    let Some(provider) = provider else {
        return Err(format!(
            "missing llm provider for parser_mode={:?}",
            request.parser_mode
        ));
    };
    let model = request.llm_model.clone().unwrap_or_else(|| {
        model_from_env(
            Some(provider.as_str()),
            &BTreeMap::from_iter(std::env::vars().filter(|(_, value)| !value.trim().is_empty())),
        )
        .expect("resolved provider model from environment or defaults")
    });

    if signature.accepts_llm_provider || signature.supports_kwargs {
        kwargs.llm_provider = Some(provider.clone());
    }
    if signature.accepts_model || signature.supports_kwargs {
        kwargs.model = Some(model);
    }
    if signature.accepts_provider_settings || signature.supports_kwargs {
        kwargs.provider_settings = provider_settings;
    }
    if trace_log.is_some() && (signature.accepts_trace_log || signature.supports_kwargs) {
        kwargs.trace_log = trace_log;
    }
    if !signature.supports_kwargs
        && kwargs.llm_provider.is_none()
        && kwargs.model.is_none()
        && kwargs.provider_settings.is_none()
    {
        return Err(
            "Configured parser does not expose llm_provider/model or provider_settings; \
             upgrade kg-doc-parser or provide a compatible parser callable."
                .to_owned(),
        );
    }
    Ok(kwargs)
}

#[must_use]
pub fn parser_kwargs_value(kwargs: &ParserKwargs) -> Value {
    json!({
        "document_id": kwargs.document_id,
        "title": kwargs.title,
        "raw_text": kwargs.raw_text,
        "source_format": kwargs.source_format,
        "mode": kwargs.mode,
        "llm_provider": kwargs.llm_provider,
        "model": kwargs.model,
        "provider_settings": kwargs.provider_settings,
        "trace_log": kwargs.trace_log,
    })
}
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
