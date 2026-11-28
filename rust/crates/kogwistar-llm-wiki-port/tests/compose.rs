use kogwistar_llm_wiki_port::compose::{check_compose_text, validate_options, ComposeOptions};
use serde_json::json;

#[test]
fn default_options_require_model_revision() {
    let options = ComposeOptions::new();
    assert_eq!(
        validate_options(&options),
        vec!["model_revision is required for the multimodal service"]
    );
}

#[test]
fn validate_options_reports_exact_python_errors() {
    let options = ComposeOptions {
        workspace: String::new(),
        project_name: "-bad".to_owned(),
        mode: "invalid".to_owned(),
        embedding_backend: "invalid".to_owned(),
        backend: "invalid".to_owned(),
        auth_mode: "invalid".to_owned(),
        model_revision: String::new(),
        embedding_dimension: 2049,
        embedding_max_model_len: 0,
        embedding_crop_token_budget: -1,
        embedding_gpu_memory_utilization: 0.0,
        embedding_vllm_max_num_seqs: 0,
        ..ComposeOptions::new()
    };
    assert_eq!(
        validate_options(&options),
        vec![
            "workspace must contain only letters, numbers, '.', '_', or '-'",
            "project_name must contain only letters, numbers, '.', '_', or '-'",
            "mode must be cpu, gpu, or text-only",
            "embedding_backend must be auto, vllm, or transformers",
            "backend must be postgres, chroma, pinecone, or qdrant",
            "auth_mode must be disabled, static_token, or kogwistar_jwt",
            "embedding_dimension must be between 64 and 2048",
            "embedding_max_model_len must be positive",
            "embedding_crop_token_budget must be positive",
            "embedding_gpu_memory_utilization must be between 0 and 1",
            "embedding_vllm_max_num_seqs must be positive"
        ]
    );
}

#[test]
fn compose_text_reports_missing_stack_elements() {
    let result = check_compose_text("services:\n  web:\n    image: example\n");
    assert_eq!(result["valid"], json!(false));
    assert_eq!(
        result["errors"][0],
        json!("missing required Compose element: llm-wiki:")
    );
    assert_eq!(result["checks"]["services:"], json!("ok"));
    assert_eq!(result["checks"]["llm-wiki:"], json!("missing"));
}

#[test]
fn compose_text_enforces_postgres_and_cuda_contracts() {
    let missing = check_compose_text("services:\nllm-wiki:\npostgres:\n");
    assert_eq!(
        missing["errors"],
        json!([
            "missing required PostgreSQL element: KOGWISTAR_POSTGRES_DSN",
            "missing required PostgreSQL element: healthcheck:"
        ])
    );
    let cuda = check_compose_text(
        "services:\nllm-wiki:\nLLM_WIKI_EMBEDDING_DEVICE: cuda\nQwen3-VL-Embedding-2B\n",
    );
    assert_eq!(
        cuda["errors"],
        json!([
            "CUDA embedding service requires an NVIDIA device reservation",
            "Qwen3-VL embedding requires an immutable model revision"
        ])
    );
}
