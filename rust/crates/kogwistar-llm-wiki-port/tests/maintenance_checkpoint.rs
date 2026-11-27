use kogwistar_llm_wiki_port::maintenance_checkpoint::{
    make_checkpoint, CheckpointStatus, CheckpointStore, MAX_CHECKPOINT_HISTORY,
};

#[test]
fn checkpoint_store_restores_last_valid_checkpoint() {
    let mut store = CheckpointStore::new();
    store.save(make_checkpoint(
        "c1",
        "ws-1",
        1,
        &serde_json::json!({"a": 1}),
        CheckpointStatus::Valid,
    ));
    store.save(make_checkpoint(
        "c2",
        "ws-1",
        2,
        &serde_json::json!({"a": 2}),
        CheckpointStatus::Invalid,
    ));
    store.save(make_checkpoint(
        "c3",
        "ws-1",
        3,
        &serde_json::json!({"a": 3}),
        CheckpointStatus::Valid,
    ));
    let restored = store.restore_last_valid().unwrap();
    assert_eq!(restored.id, "c3");
    assert_eq!(restored.seq, 3);
}

#[test]
fn checkpoint_store_returns_none_when_no_valid_checkpoint() {
    let mut store = CheckpointStore::new();
    store.save(make_checkpoint(
        "c1",
        "ws-1",
        1,
        &serde_json::json!({}),
        CheckpointStatus::Invalid,
    ));
    assert_eq!(store.restore_last_valid(), None);
    assert!(!store.is_empty());
}

#[test]
fn checkpoint_store_rejects_invalid_checkpoint() {
    let mut store = CheckpointStore::new();
    store.save(make_checkpoint(
        "c1",
        "ws-1",
        1,
        &serde_json::json!({}),
        CheckpointStatus::Invalid,
    ));
    assert!(store.reject_invalid("c1"));
    assert!(!store.reject_invalid("c2"));
}

#[test]
fn checkpoint_store_bounds_history_to_max() {
    let mut store = CheckpointStore::new();
    for i in 1..=7 {
        store.save(make_checkpoint(
            &format!("c{i}"),
            "ws-1",
            i,
            &serde_json::json!({}),
            CheckpointStatus::Valid,
        ));
    }
    assert_eq!(store.len(), MAX_CHECKPOINT_HISTORY);
    assert_eq!(store.restore_last_valid().unwrap().id, "c7");
}
