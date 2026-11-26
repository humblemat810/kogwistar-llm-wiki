use kogwistar_llm_wiki_port::maintenance_guards::{
    build_source_revision, evaluate_maintenance_guard, evaluate_parse_session_guard, readiness_id,
    required_stage_for_maintenance, source_digest, MaintenanceGuardDecision,
};

#[test]
fn guard_stage_and_readiness_ids_match_python_contracts() {
    assert_eq!(
        required_stage_for_maintenance("document_seed_graph"),
        "source_map_seeded"
    );
    assert_eq!(
        required_stage_for_maintenance("document_validate_crosslinks"),
        "parsed_graph_persisted"
    );
    assert_eq!(
        source_digest(""),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
    let revision = build_source_revision("ws", "doc", "text", None);
    assert_eq!(
        readiness_id(
            "doc",
            revision.revision_id.as_str(),
            "parsed_graph_persisted"
        ),
        kogwistar_llm_wiki_port::ids::stable_id(
            "kogwistar_llm_wiki.source_readiness",
            &[
                "doc",
                revision.revision_id.as_str(),
                "parsed_graph_persisted"
            ]
        )
        .to_string()
    );
}

#[test]
fn guard_blocks_missing_revision_and_readiness() {
    let decision = evaluate_maintenance_guard(None, "rev", "digest", None, "stage", &[]);
    assert_eq!(decision.status, "blocked");
    assert_eq!(decision.reason, "source_revision_missing");

    let revision = build_source_revision("ws", "doc", "text", None);
    let decision = evaluate_maintenance_guard(
        Some(&revision),
        revision.revision_id.as_str(),
        revision.source_digest.as_str(),
        None,
        "parsed_graph_persisted",
        &[],
    );
    assert_eq!(decision.status, "blocked");
    assert_eq!(
        decision.reason,
        "required_stage_missing:parsed_graph_persisted"
    );
}

#[test]
fn guard_rejects_stale_revision_and_document() {
    let revision = build_source_revision("ws", "doc", "text", None);
    let decision = evaluate_maintenance_guard(
        Some(&revision),
        "old-revision",
        "old-digest",
        None,
        "parsed_graph_persisted",
        &["old-revision".to_owned()],
    );
    assert_eq!(decision.status, "stale");
    assert_eq!(decision.reason, "source_revision_mismatch");

    let decision = evaluate_maintenance_guard(
        Some(&revision),
        revision.revision_id.as_str(),
        revision.source_digest.as_str(),
        Some("wrong-document"),
        "parsed_graph_persisted",
        std::slice::from_ref(&revision.revision_id),
    );
    assert_eq!(decision.status, "stale");
    assert_eq!(decision.reason, "source_revision_document_mismatch");
}

#[test]
fn parse_session_guard_supersedes_only_ready_decisions() {
    let decision = MaintenanceGuardDecision {
        status: "ready",
        reason: "source_revision_and_readiness_verified".to_owned(),
        source_document_id: "doc".to_owned(),
        source_revision_id: "rev".to_owned(),
        source_digest: "digest".to_owned(),
        required_stage: "source_map_seeded".to_owned(),
    };
    assert_eq!(
        evaluate_parse_session_guard(&decision, "old", Some("new")).reason,
        "parse_session_superseded"
    );
    assert_eq!(
        evaluate_parse_session_guard(&decision, "new", Some("new")).reason,
        decision.reason
    );
}
