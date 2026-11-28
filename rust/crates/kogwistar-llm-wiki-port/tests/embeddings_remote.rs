use kogwistar_llm_wiki_port::embeddings::remote::AssetResolver;
use kogwistar_llm_wiki_port::embeddings::{
    EmbeddingServiceError, EmbeddingServiceSettings, HttpResponseLike, RemoteMultimodalEncoder,
    UrlOpener,
};
use kogwistar_llm_wiki_port::multimodal_projection::{
    MultimodalEmbeddingProfile, MultimodalEncoder, MultimodalSourceUnit, SimilarityMetric,
    SourceModality,
};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

struct FakeResponse {
    status: u16,
    body: Vec<u8>,
}

impl HttpResponseLike for FakeResponse {
    fn status(&self) -> u16 {
        self.status
    }

    fn body(&self) -> &[u8] {
        &self.body
    }
}

#[derive(Clone)]
#[allow(clippy::type_complexity)]
struct FakeOpener {
    responses: Arc<Mutex<Vec<Result<(u16, Vec<u8>), EmbeddingServiceError>>>>,
    calls: Arc<AtomicU32>,
}

impl FakeOpener {
    fn new(responses: Vec<Result<(u16, Vec<u8>), EmbeddingServiceError>>) -> Self {
        Self {
            responses: Arc::new(Mutex::new(responses)),
            calls: Arc::new(AtomicU32::new(0)),
        }
    }

    fn calls(&self) -> u32 {
        self.calls.load(Ordering::SeqCst)
    }
}

impl UrlOpener for FakeOpener {
    fn open(
        &self,
        _url: &str,
        _timeout_seconds: f64,
    ) -> Result<Box<dyn HttpResponseLike>, EmbeddingServiceError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        match self.responses.lock().unwrap().remove(0) {
            Ok((status, body)) => Ok(Box::new(FakeResponse { status, body })),
            Err(error) => Err(error),
        }
    }
}

fn test_profile() -> MultimodalEmbeddingProfile {
    MultimodalEmbeddingProfile {
        model: "test-embed".to_string(),
        revision: Some("v1".to_string()),
        dimension: 3,
        metric: SimilarityMetric::Cosine,
        max_sequence_length: None,
        crop_token_budget: None,
    }
}

fn success_body(profile: &MultimodalEmbeddingProfile, results: Vec<Value>) -> Vec<u8> {
    serde_json::to_string(&json!({
        "contract_version": "1",
        "profile": { "fingerprint": profile.fingerprint() },
        "results": results,
    }))
    .unwrap()
    .into_bytes()
}

fn valid_settings() -> EmbeddingServiceSettings {
    EmbeddingServiceSettings::new("https://example.com/embed", None, 30.0, 10000, None, vec![])
        .unwrap()
}

#[test]
fn settings_rejects_non_http_url() {
    let err =
        EmbeddingServiceSettings::new("ftp://example.com/embed", None, 30.0, 1000, None, vec![])
            .unwrap_err();
    assert!(err.is_protocol());
    assert!(!err.is_unavailable());
}

#[test]
fn settings_rejects_nonpositive_limits() {
    let err =
        EmbeddingServiceSettings::new("https://example.com/embed", None, 0.0, 1000, None, vec![])
            .unwrap_err();
    assert!(err.is_protocol());

    let err =
        EmbeddingServiceSettings::new("https://example.com/embed", None, 30.0, 0, None, vec![])
            .unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn settings_rejects_host_not_allowlisted() {
    let err = EmbeddingServiceSettings::new(
        "https://evil.example.com/embed",
        None,
        30.0,
        1000,
        None,
        vec!["example.com".to_string()],
    )
    .unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn settings_accepts_valid() {
    let settings = valid_settings();
    assert_eq!(settings.url, "https://example.com/embed");
}

#[test]
fn encode_queries_success_matches_item_order_and_fingerprint() {
    let profile = test_profile();
    let fingerprint = profile.fingerprint();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        success_body(
            &profile,
            vec![
                json!({ "item_id": "query-0", "vectors": [[1.0, 2.0, 3.0]] }),
                json!({ "item_id": "query-1", "vectors": [[4.0, 5.0, 6.0]] }),
            ],
        ),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile.clone(), None);
    let sets = encoder
        .encode_queries(&["alpha".to_string(), "beta".to_string()])
        .unwrap();
    assert_eq!(sets.len(), 2);
    assert_eq!(sets[0].view_id, "query-0");
    assert_eq!(sets[0].vectors, vec![vec![1.0, 2.0, 3.0]]);
    assert_eq!(sets[1].view_id, "query-1");
    assert_eq!(sets[1].vectors, vec![vec![4.0, 5.0, 6.0]]);
    assert_eq!(sets[0].profile_fingerprint, fingerprint);
}

#[test]
fn encode_queries_rejects_contract_version_mismatch() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        serde_json::to_string(&json!({
            "contract_version": "2",
            "profile": { "fingerprint": profile.fingerprint() },
            "results": [],
        }))
        .unwrap()
        .into_bytes(),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let err = encoder.encode_queries(&["x".to_string()]).unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn encode_queries_rejects_profile_fingerprint_mismatch() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        serde_json::to_string(&json!({
            "contract_version": "1",
            "profile": { "fingerprint": "wrong-fingerprint" },
            "results": [],
        }))
        .unwrap()
        .into_bytes(),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let err = encoder.encode_queries(&["x".to_string()]).unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn encode_queries_rejects_unavailable_on_server_error() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((500, b"internal error".to_vec()))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let err = encoder.encode_queries(&["x".to_string()]).unwrap_err();
    assert!(err.is_unavailable());
    assert!(!err.is_protocol());
}

#[test]
fn encode_queries_rejects_invalid_json() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((200, b"{not json".to_vec()))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let err = encoder.encode_queries(&["x".to_string()]).unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn encode_queries_rejects_result_count_mismatch() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        success_body(
            &profile,
            vec![json!({ "item_id": "query-0", "vectors": [[1.0, 2.0, 3.0]] })],
        ),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let err = encoder
        .encode_queries(&["a".to_string(), "b".to_string()])
        .unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn encode_queries_rejects_item_ordering_mismatch() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        success_body(
            &profile,
            vec![
                json!({ "item_id": "query-1", "vectors": [[1.0, 2.0, 3.0]] }),
                json!({ "item_id": "query-0", "vectors": [[4.0, 5.0, 6.0]] }),
            ],
        ),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let err = encoder
        .encode_queries(&["a".to_string(), "b".to_string()])
        .unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn encode_queries_rejects_dimension_mismatch() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        success_body(
            &profile,
            vec![json!({ "item_id": "query-0", "vectors": [[1.0, 2.0]] })],
        ),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let err = encoder.encode_queries(&["a".to_string()]).unwrap_err();
    assert!(err.is_protocol());
}

#[test]
fn encode_queries_rejects_request_too_large() {
    let profile = test_profile();
    let mut settings = valid_settings();
    settings.max_request_bytes = 1;
    let opener = FakeOpener::new(vec![Ok((200, success_body(&profile, vec![])))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener.clone()), profile, None);
    let err = encoder.encode_queries(&["a".to_string()]).unwrap_err();
    assert!(err.is_protocol());
    assert_eq!(opener.calls(), 0);
}

#[test]
fn encode_documents_success_without_asset() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        success_body(
            &profile,
            vec![json!({
                "item_id": "doc-1",
                "vectors": [[0.5, 0.5, 0.5], [0.25, 0.25, 0.25]]
            })],
        ),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener), profile, None);
    let unit = MultimodalSourceUnit::new("doc-1", "ws-1", SourceModality::Text);
    let out = encoder.encode_documents(&[unit]).unwrap();
    assert_eq!(out.len(), 1);
    assert_eq!(out[0].len(), 2);
    assert_eq!(out[0], vec![vec![0.5, 0.5, 0.5], vec![0.25, 0.25, 0.25]]);
}

struct FixedResolver;

impl AssetResolver for FixedResolver {
    fn resolve(
        &self,
        _unit: &MultimodalSourceUnit,
    ) -> Result<Vec<u8>, kogwistar_llm_wiki_port::multimodal_projection::ProjectionIntegrityError>
    {
        Ok(b"fixed-bytes".to_vec())
    }
}

#[test]
fn encode_documents_success_with_resolver() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        success_body(
            &profile,
            vec![json!({ "item_id": "doc-1", "vectors": [[1.0, 0.0, 0.0]] })],
        ),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(
        settings,
        Box::new(opener),
        profile,
        Some(Box::new(FixedResolver)),
    );
    let mut unit = MultimodalSourceUnit::new("doc-1", "ws-1", SourceModality::Image);
    unit.content_ref = Some("ref".to_string());
    let out = encoder.encode_documents(&[unit]).unwrap();
    assert_eq!(out.len(), 1);
    assert_eq!(out[0], vec![vec![1.0, 0.0, 0.0]]);
}

#[test]
fn encode_image_queries_success() {
    let profile = test_profile();
    let settings = valid_settings();
    let opener = FakeOpener::new(vec![Ok((
        200,
        success_body(
            &profile,
            vec![json!({ "item_id": "image-query-0", "vectors": [[9.0, 9.0, 9.0]] })],
        ),
    ))]);
    let encoder = RemoteMultimodalEncoder::new(settings, Box::new(opener.clone()), profile, None);
    let sets = encoder.encode_image_queries(&[vec![1, 2, 3]]).unwrap();
    assert_eq!(sets.len(), 1);
    assert_eq!(sets[0].view_id, "image-query-0");
    assert_eq!(sets[0].vectors, vec![vec![9.0, 9.0, 9.0]]);
    assert_eq!(opener.calls(), 1);
}
