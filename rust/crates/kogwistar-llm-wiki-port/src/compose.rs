use serde::Serialize;
use serde_json::{json, Map, Value};

#[derive(Clone, Debug, PartialEq)]
pub struct ComposeOptions {
    pub backend: String,
    pub workspace: String,
    pub project_name: String,
    pub mode: String,
    pub embedding_backend: String,
    pub with_otel: bool,
    pub with_oauth: bool,
    pub auth_mode: String,
    pub model_revision: String,
    pub embedding_dimension: i64,
    pub embedding_max_model_len: i64,
    pub embedding_crop_token_budget: i64,
    pub embedding_gpu_memory_utilization: f64,
    pub embedding_vllm_enforce_eager: bool,
    pub embedding_vllm_max_num_seqs: i64,
    pub postgres_password: String,
}

impl Default for ComposeOptions {
    fn default() -> Self {
        Self::new()
    }
}

impl ComposeOptions {
    #[must_use]
    pub fn new() -> Self {
        Self {
            backend: "postgres".to_owned(),
            workspace: "default".to_owned(),
            project_name: "llm-wiki".to_owned(),
            mode: "gpu".to_owned(),
            embedding_backend: "auto".to_owned(),
            with_otel: false,
            with_oauth: false,
            auth_mode: "disabled".to_owned(),
            model_revision: String::new(),
            embedding_dimension: 1024,
            embedding_max_model_len: 512,
            embedding_crop_token_budget: 480,
            embedding_gpu_memory_utilization: 0.80,
            embedding_vllm_enforce_eager: true,
            embedding_vllm_max_num_seqs: 1,
            postgres_password: "change-this-development-password".to_owned(),
        }
    }
}

fn identifier_valid(value: &str) -> bool {
    let mut characters = value.chars();
    let Some(first) = characters.next() else {
        return false;
    };
    first.is_ascii_alphanumeric()
        && value.len() <= 63
        && value.chars().skip(1).all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
        })
}

#[must_use]
pub fn validate_options(options: &ComposeOptions) -> Vec<String> {
    let mut errors = Vec::new();
    if !identifier_valid(&options.workspace) {
        errors.push("workspace must contain only letters, numbers, '.', '_', or '-'".to_owned());
    }
    if !identifier_valid(&options.project_name) {
        errors.push("project_name must contain only letters, numbers, '.', '_', or '-'".to_owned());
    }
    if !matches!(options.mode.as_str(), "cpu" | "gpu" | "text-only") {
        errors.push("mode must be cpu, gpu, or text-only".to_owned());
    }
    if !matches!(
        options.embedding_backend.as_str(),
        "auto" | "vllm" | "transformers"
    ) {
        errors.push("embedding_backend must be auto, vllm, or transformers".to_owned());
    }
    if options.mode == "text-only" && options.embedding_backend != "auto" {
        errors.push("embedding_backend is only valid for cpu or gpu multimodal modes".to_owned());
    }
    if options.mode == "cpu" && options.embedding_backend == "vllm" {
        errors.push("vllm embedding_backend is GPU-only; use transformers for CPU".to_owned());
    }
    if !matches!(
        options.backend.as_str(),
        "postgres" | "chroma" | "pinecone" | "qdrant"
    ) {
        errors.push("backend must be postgres, chroma, pinecone, or qdrant".to_owned());
    } else if options.backend == "chroma" {
        errors.push(
            "embedded Chroma cannot be generated for the multi-process Compose bundle; \
             use PostgreSQL or the single-process demo"
                .to_owned(),
        );
    }
    if !matches!(
        options.auth_mode.as_str(),
        "disabled" | "static_token" | "kogwistar_jwt"
    ) {
        errors.push("auth_mode must be disabled, static_token, or kogwistar_jwt".to_owned());
    }
    if matches!(options.mode.as_str(), "cpu" | "gpu") && options.model_revision.trim().is_empty() {
        errors.push("model_revision is required for the multimodal service".to_owned());
    }
    if !(64..=2048).contains(&options.embedding_dimension) {
        errors.push("embedding_dimension must be between 64 and 2048".to_owned());
    }
    if options.backend == "postgres" && options.mode == "gpu" && options.embedding_dimension > 1536
    {
        errors.push("GPU pgvector default profile cannot use dimensions above 1536".to_owned());
    }
    if options.embedding_max_model_len <= 0 {
        errors.push("embedding_max_model_len must be positive".to_owned());
    } else if options.embedding_max_model_len > 8192 {
        errors.push("embedding_max_model_len cannot exceed 8192".to_owned());
    }
    if options.embedding_crop_token_budget <= 0 {
        errors.push("embedding_crop_token_budget must be positive".to_owned());
    } else if options.embedding_crop_token_budget > options.embedding_max_model_len {
        errors.push("embedding_crop_token_budget cannot exceed embedding_max_model_len".to_owned());
    }
    if !(options.embedding_gpu_memory_utilization > 0.0
        && options.embedding_gpu_memory_utilization <= 1.0)
    {
        errors.push("embedding_gpu_memory_utilization must be between 0 and 1".to_owned());
    }
    if options.embedding_vllm_max_num_seqs <= 0 {
        errors.push("embedding_vllm_max_num_seqs must be positive".to_owned());
    }
    errors
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ComposeTextCheck {
    pub valid: bool,
    pub errors: Vec<String>,
    pub checks: Map<String, Value>,
}

fn check(text: &str, token: &str, checks: &mut Map<String, Value>, errors: &mut Vec<String>) {
    let present = text.contains(token);
    checks.insert(
        token.to_owned(),
        Value::String(if present { "ok" } else { "missing" }.to_owned()),
    );
    if !present {
        errors.push(format!("missing required Compose element: {token}"));
    }
}

#[must_use]
pub fn check_compose_text(text: &str) -> Value {
    let mut checks = Map::new();
    let mut errors = Vec::new();
    for token in ["services:", "llm-wiki:"] {
        check(text, token, &mut checks, &mut errors);
    }
    if text.contains("postgres:") {
        for token in ["KOGWISTAR_POSTGRES_DSN", "healthcheck:"] {
            let present = text.contains(token);
            checks.insert(
                token.to_owned(),
                Value::String(if present { "ok" } else { "missing" }.to_owned()),
            );
            if !present {
                errors.push(format!("missing required PostgreSQL element: {token}"));
            }
        }
    }
    if text.contains("LLM_WIKI_EMBEDDING_DEVICE: cuda") && !text.contains("driver: nvidia") {
        errors.push("CUDA embedding service requires an NVIDIA device reservation".to_owned());
    }
    if text.contains("Qwen3-VL-Embedding-2B")
        && !text.contains("LLM_WIKI_MULTIMODAL_MODEL_REVISION")
        && !text.contains("LLM_WIKI_EMBEDDING_MODEL_REVISION")
    {
        errors.push("Qwen3-VL embedding requires an immutable model revision".to_owned());
    }
    json!({
        "valid": errors.is_empty(),
        "errors": errors,
        "checks": checks
    })
}
