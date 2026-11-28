use kogwistar_llm_wiki_port::archiving::{
    artifact_dirs, embedding_fingerprint, readable_archive_format, restore_report_wire,
    safe_relative_member, secret_name, ArchiveError, RestoreReport,
};
use kogwistar_llm_wiki_port::models::JsonObject;
use serde_json::json;

#[test]
fn archive_dirs_format_and_secrets_match_python() {
    assert_eq!(
        artifact_dirs(),
        [
            "raw_documents",
            "source_artifacts",
            "parser_runs",
            "workflow_runs",
            "maintenance"
        ]
    );
    assert!(readable_archive_format(1));
    assert!(readable_archive_format(2));
    assert!(!readable_archive_format(3));
    assert!(secret_name(".env.local"));
    assert!(!secret_name("document.pdf"));
}

#[test]
fn safe_relative_member_accepts_forward_and_windows_paths() {
    assert_eq!(
        safe_relative_member("artifacts/source_artifacts/file.json", "artifacts"),
        Ok(std::path::PathBuf::from("source_artifacts/file.json"))
    );
    assert_eq!(
        safe_relative_member("artifacts\\maintenance\\event.json", "artifacts"),
        Ok(std::path::PathBuf::from("maintenance/event.json"))
    );
}

#[test]
fn safe_relative_member_rejects_escape_paths() {
    assert_eq!(
        safe_relative_member("artifacts/../secret.json", "artifacts"),
        Err(ArchiveError::UnsafePath {
            name: "artifacts/../secret.json".to_owned(),
            reason: "parent traversal"
        })
    );
    assert!(matches!(
        safe_relative_member("/absolute/file.json", "artifacts"),
        Err(ArchiveError::UnsafePath { .. })
    ));
    assert!(matches!(
        safe_relative_member("artifacts/source/file.json", "public"),
        Err(ArchiveError::OutsidePrefix { .. })
    ));
}

#[test]
fn restore_report_uses_python_wire_fields() {
    let report = RestoreReport {
        archive_id: "archive-1".to_owned(),
        workspace_id: "old".to_owned(),
        target_workspace_id: "new".to_owned(),
        dry_run: true,
        imported_events: 4,
        replayed_namespaces: 2,
        rebuilt_vectors: false,
    };
    assert_eq!(
        restore_report_wire(&report),
        json!({
            "archive_id": "archive-1",
            "workspace_id": "old",
            "target_workspace_id": "new",
            "dry_run": true,
            "imported_events": 4,
            "replayed_namespaces": 2,
            "rebuilt_vectors": false
        })
    );
}

#[test]
fn embedding_fingerprint_matches_python_sorted_non_ascii_json() {
    let profiles: JsonObject = serde_json::from_value(json!({
        "b-profile": {"model": "x", "dimension": 2},
        "a-profile": {"model": "y", "dimension": 3}
    }))
    .unwrap();
    assert_eq!(
        embedding_fingerprint(&profiles),
        "2da5e0fa53c949204aa09d4ee47286bf27f1441f1077c3ea1facaefb38cf4068"
    );
}
