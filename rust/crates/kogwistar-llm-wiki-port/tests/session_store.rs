use kogwistar_llm_wiki_port::parsing::session_store::{
    parse_session_scope_id, NamedProjectionStore, ParseFrontierItem, ParseFrontierStatus,
    ParseSessionPhase, ParseSessionState, ParseSessionStore, ParseSessionStoreError, ProjectionRow,
};
use kogwistar_llm_wiki_port::parsing::SourceRegion;
use serde_json::json;
use std::collections::BTreeMap;
use std::sync::Mutex;

struct FakeStore {
    rows: Mutex<BTreeMap<String, serde_json::Value>>,
}

impl FakeStore {
    fn new() -> Self {
        Self {
            rows: Mutex::new(BTreeMap::new()),
        }
    }
}

impl NamedProjectionStore for FakeStore {
    fn get_named_projection(&self, _namespace: &str, key: &str) -> Option<ProjectionRow> {
        let rows = self.rows.lock().unwrap();
        let row = rows.get(key)?;
        Some(ProjectionRow {
            payload: row.get("payload")?.as_object()?.clone(),
            last_authoritative_seq: row
                .get("last_authoritative_seq")
                .and_then(|value| value.as_i64()),
            last_materialized_seq: row
                .get("last_materialized_seq")
                .and_then(|value| value.as_i64()),
        })
    }

    fn compare_and_swap_named_projection(
        &self,
        _namespace: &str,
        key: &str,
        payload: serde_json::Value,
        expected_last_authoritative_seq: Option<i64>,
        expected_last_materialized_seq: Option<i64>,
        last_authoritative_seq: i64,
        last_materialized_seq: i64,
        _projection_schema_version: u32,
        _materialization_status: &str,
    ) -> bool {
        let mut rows = self.rows.lock().unwrap();
        let current = rows.get(key);
        let cur_auth = current.and_then(|row| {
            row.get("last_authoritative_seq")
                .and_then(|value| value.as_i64())
        });
        let cur_mat = current.and_then(|row| {
            row.get("last_materialized_seq")
                .and_then(|value| value.as_i64())
        });
        if cur_auth != expected_last_authoritative_seq || cur_mat != expected_last_materialized_seq
        {
            return false;
        }
        rows.insert(
            key.to_string(),
            json!({
                "payload": payload,
                "last_authoritative_seq": last_authoritative_seq,
                "last_materialized_seq": last_materialized_seq,
            }),
        );
        true
    }
}

fn test_session() -> ParseSessionState {
    ParseSessionState {
        session_id: "sess:1".into(),
        workspace_id: "ws:1".into(),
        source_document_id: "doc:1".into(),
        source_revision_id: "rev:1".into(),
        source_digest: "digest:1".into(),
        revision_document_id: "doc:1".into(),
        generation_id: "gen:1".into(),
        phase: ParseSessionPhase::Seeded,
        frontier_ids: vec![],
        consumed_frontier_ids: vec![],
        max_depth: 0,
        max_frontier_items: 1,
        max_parser_calls: 1000,
        max_region_chars: 16384,
        parser_calls: 0,
        token_budget: None,
        wall_time_seconds: None,
        last_progress_at: None,
        failure_reason: None,
        parser_state: json!({}),
        pending_view: None,
        dependents_enqueued_view_version: None,
    }
}

fn test_frontier_item() -> ParseFrontierItem {
    ParseFrontierItem {
        frontier_id: "front:1".into(),
        session_id: "sess:1".into(),
        generation_id: "gen:1".into(),
        workspace_id: "ws:1".into(),
        source_document_id: "doc:1".into(),
        source_revision_id: "rev:1".into(),
        revision_document_id: "doc:1".into(),
        parent_member_id: None,
        region: SourceRegion::new("doc:1", 0, 10).unwrap(),
        depth: 0,
        ordinal: 0,
        status: ParseFrontierStatus::Pending,
        attempt: 0,
        last_error: None,
    }
}

#[test]
fn scope_id_matches_python() {
    assert_eq!(parse_session_scope_id(None), "full");
    let region = SourceRegion::new("rev:1", 10, 20).unwrap();
    assert_eq!(parse_session_scope_id(Some(&region)), "region:10:20");
}

#[test]
fn active_session_id_absent_when_no_row() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    assert_eq!(ss.active_session_id("doc:1", "full"), None);
}

#[test]
fn save_first_write_returns_version_one() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let session = test_session();
    let version = ss.save(&session, &[], None).unwrap();
    assert_eq!(version, 1);
}

#[test]
fn save_version_mismatch_conflicts() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let session = test_session();
    assert!(matches!(
        ss.save(&session, &[], Some(5)),
        Err(ParseSessionStoreError::Conflict)
    ));
}

#[test]
fn save_correct_version_advances() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let session = test_session();
    assert_eq!(ss.save(&session, &[], None).unwrap(), 1);
    let mut next = session.clone();
    next.parser_calls = 1;
    assert_eq!(ss.save(&next, &[], Some(1)).unwrap(), 2);
}

#[test]
fn save_rejects_workspace_mismatch() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let mut other = test_session();
    other.workspace_id = "ws:2".into();
    assert!(matches!(
        ss.save(&other, &[], None),
        Err(ParseSessionStoreError::Invalid(_))
    ));
}

#[test]
fn save_rejects_frontier_pointer_mismatch() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let mut session = test_session();
    session.frontier_ids = vec!["front:2".into()];
    let item = test_frontier_item();
    assert!(ss.save(&session, &[item], None).is_err());
}

#[test]
fn activate_round_trip_and_active_session_id() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let session = test_session();
    ss.save(&session, &[], None).unwrap();
    ss.activate(&session, "full").unwrap();
    assert_eq!(
        ss.active_session_id("doc:1", "full"),
        Some("sess:1".to_string())
    );
}

#[test]
fn activate_unknown_session_rejected() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let session = test_session();
    assert!(matches!(
        ss.activate(&session, "full"),
        Err(ParseSessionStoreError::Invalid(_))
    ));
}

#[test]
fn activate_scope_isolation() {
    let store = FakeStore::new();
    let ss = ParseSessionStore::new(store, "ws:1");
    let session = test_session();
    ss.save(&session, &[], None).unwrap();
    ss.activate(&session, "region:0:10").unwrap();
    assert_eq!(ss.active_session_id("doc:1", "full"), None);
    assert_eq!(
        ss.active_session_id("doc:1", "region:0:10"),
        Some("sess:1".to_string())
    );
}
