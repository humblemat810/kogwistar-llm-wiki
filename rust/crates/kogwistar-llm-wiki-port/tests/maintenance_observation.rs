use kogwistar_llm_wiki_port::maintenance_observation::{
    assess_observation_frame, build_observation_frame, AssessmentInputs, ObservationFrameInputs,
    ObservationSubject,
};
use serde_json::{json, Value};

fn subject() -> ObservationSubject {
    serde_json::from_value(json!({
        "kind": "node",
        "subject_id": "node-1",
        "workspace_id": "demo",
        "namespace": "ws:demo:source"
    }))
    .unwrap()
}

fn frame_inputs<'a>(
    subject: &'a ObservationSubject,
    source: &'a [Value],
    token_budget: i64,
) -> ObservationFrameInputs<'a> {
    ObservationFrameInputs {
        subject,
        source_context: source,
        relation_context: &[],
        neighborhood_context: &[],
        parent_context: &[],
        token_budget,
        active_view_id: None,
        active_view_version: None,
        source_evidence_required: false,
    }
}

#[test]
fn observation_frame_is_deterministic_and_bounded() {
    let subject = subject();
    let neighborhood = (0..100)
        .map(|index| json!({"node_id": format!("node-{index}")}))
        .collect::<Vec<_>>();
    let first = build_observation_frame(&ObservationFrameInputs {
        subject: &subject,
        source_context: &[],
        relation_context: &[],
        neighborhood_context: &neighborhood,
        parent_context: &[],
        token_budget: 256,
        active_view_id: None,
        active_view_version: None,
        source_evidence_required: false,
    });
    let repeat = build_observation_frame(&ObservationFrameInputs {
        subject: &subject,
        source_context: &[],
        relation_context: &[],
        neighborhood_context: &neighborhood,
        parent_context: &[],
        token_budget: 256,
        active_view_id: None,
        active_view_version: None,
        source_evidence_required: false,
    });
    assert_eq!(first.frame_id, repeat.frame_id);
    assert!(first.omitted_counts["neighborhood"] > 0);
    assert!(first.neighborhood_context.len() < 100);
}

#[test]
fn observation_frame_preserves_only_scoped_relation_evidence() {
    let subject: ObservationSubject = serde_json::from_value(json!({
        "kind": "edge",
        "subject_id": "edge-1",
        "workspace_id": "demo",
        "namespace": "ws:demo:g:curated_kg"
    }))
    .unwrap();
    let relations = vec![
        json!({"relation_id": "edge-other", "workspace_id": "other", "evidence_ids": ["doc-1"]}),
        json!({"relation_id": "edge-unauthorized", "acl_authorized": false, "evidence_ids": ["doc-2"]}),
        json!({"relation_id": "edge-local"}),
    ];
    let frame = build_observation_frame(&ObservationFrameInputs {
        subject: &subject,
        source_context: &[],
        relation_context: &relations,
        neighborhood_context: &[],
        parent_context: &[],
        token_budget: 4000,
        active_view_id: None,
        active_view_version: None,
        source_evidence_required: false,
    });
    assert_eq!(frame.relation_context.len(), 1);
    assert_eq!(
        frame.relation_context[0]["relation_id"],
        json!("edge-local")
    );
}

#[test]
fn uncertain_parse_never_becomes_adequate_or_auto_repaired() {
    let subject: ObservationSubject = serde_json::from_value(json!({
        "kind": "edge",
        "subject_id": "edge-1",
        "workspace_id": "demo",
        "namespace": "ws:demo:source"
    }))
    .unwrap();
    let source = vec![json!({"member_id": "member-1", "quality_status": "expanding"})];
    let frame = build_observation_frame(&frame_inputs(&subject, &source, 4000));
    let assessment = assess_observation_frame(&AssessmentInputs {
        frame: &frame,
        critic_failed: false,
        critic_status: "not_used",
        critic_findings: &[],
        watermark_expiry_seconds: 86400,
        now_epoch: 1700000000,
    });
    assert_eq!(assessment.verdict, "review_required");
    assert_eq!(assessment.recommended_action, "review_parent");
    assert!(assessment.continuation_allowed);
}
