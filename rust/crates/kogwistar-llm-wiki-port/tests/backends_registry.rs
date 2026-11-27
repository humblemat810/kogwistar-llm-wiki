use kogwistar_llm_wiki_port::backends::{
    build_backend_factory, from_env_vars, BackendFactory, VectorBackendSettings, SUPPORTED_BACKENDS,
};
use serde_json::json;
use std::collections::HashMap;

#[test]
fn supported_backends_match_the_python_contract() {
    assert_eq!(
        SUPPORTED_BACKENDS,
        ["chroma", "postgres", "pinecone", "qdrant"]
    );
}

#[test]
fn settings_are_read_from_the_python_environment_names() {
    let env: HashMap<String, String> = [
        ("KOGWISTAR_VECTOR_PREFIX", "wiki"),
        ("PINECONE_INDEX_HOST", "https://index.example"),
        ("QDRANT_URL", "https://qdrant.example"),
        ("QDRANT_PATH", "/var/lib/qdrant"),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_string(), value.to_string()))
    .collect();

    let settings = from_env_vars("pinecone", &env, 768);

    assert_eq!(settings.name, "pinecone");
    assert_eq!(settings.dimension, 768);
    assert_eq!(settings.prefix, "wiki");
    assert_eq!(
        settings.pinecone_index_host.as_deref(),
        Some("https://index.example")
    );
    assert_eq!(
        settings.qdrant_url.as_deref(),
        Some("https://qdrant.example")
    );
    assert_eq!(settings.qdrant_path.as_deref(), Some("/var/lib/qdrant"));
}

#[test]
fn from_env_defaults_match_the_python_dataclass() {
    let env = HashMap::<String, String>::new();
    let settings = from_env_vars("chroma", &env, 1024);

    assert_eq!(settings.prefix, "kogwistar");
    assert_eq!(settings.pinecone_index_host, None);
    assert_eq!(settings.qdrant_url, None);
    assert_eq!(settings.qdrant_path, None);
}

#[test]
fn built_in_backends_delegate_to_the_existing_kogwistar_builders() {
    assert!(build_backend_factory(VectorBackendSettings::new("chroma", 1024)).is_ok());
    assert!(build_backend_factory(VectorBackendSettings::new("postgres", 1024)).is_ok());
}

#[test]
fn optional_provider_settings_are_preserved_for_lazy_construction() {
    let pinecone = VectorBackendSettings::new("pinecone", 768)
        .with_pinecone_index_host("https://index.example");

    assert_eq!(
        build_backend_factory(pinecone.clone()).unwrap(),
        Some(BackendFactory::Pinecone(pinecone))
    );

    let qdrant =
        VectorBackendSettings::new("qdrant", 1024).with_qdrant_url("https://qdrant.example");

    assert_eq!(
        build_backend_factory(qdrant.clone()).unwrap(),
        Some(BackendFactory::Qdrant(qdrant))
    );
}

#[test]
fn unconfigured_qdrant_refuses_an_in_memory_store() {
    let error = build_backend_factory(VectorBackendSettings::new("qdrant", 1024))
        .unwrap_err()
        .to_string();

    assert_eq!(
        error,
        "Qdrant backend requires QDRANT_URL or QDRANT_PATH; refusing an unconfigured in-memory store"
    );
}

#[test]
fn unknown_backend_error_matches_the_python_contract() {
    let error = build_backend_factory(VectorBackendSettings::new("marqo", 1024))
        .unwrap_err()
        .to_string();

    assert_eq!(
        error,
        "Unsupported vector backend 'marqo'; choose one of chroma, postgres, pinecone, qdrant"
    );
}

#[test]
fn serialized_settings_are_json_stable() {
    let settings = VectorBackendSettings::new("pinecone", 768)
        .with_pinecone_index_host("https://index.example");

    assert_eq!(
        serde_json::to_value(settings).unwrap(),
        json!({
            "name": "pinecone",
            "dimension": 768,
            "prefix": "kogwistar",
            "pinecone_index_host": "https://index.example",
            "qdrant_url": null,
            "qdrant_path": null
        })
    );
}
