use kogwistar_llm_wiki_port::parsing::{
    frontier_id, generation_id, generation_member_id, legacy_generation_id, parse_session_id,
    reparse_session_id, ParseContractError, ParseTarget, SourceRegion,
};
use serde_json::json;

fn region() -> SourceRegion {
    SourceRegion::new("rev:1", 10, 20).unwrap()
}

fn target() -> ParseTarget {
    ParseTarget::new(
        "source:1",
        "rev-id:1",
        "rev:1",
        region(),
        "reparse",
        "profile x",
        None,
        None,
        None,
        None,
        None,
        None,
    )
    .unwrap()
}

#[test]
fn parse_ids_match_python_goldens() {
    let parsed_region = region();
    assert_eq!(
        generation_id("demo", "source:1", "rev-id:1", "profile x", "session:1").to_string(),
        "c5f5b22d-8eec-5761-8811-42c0d6741946"
    );
    assert_eq!(
        parse_session_id("demo", "source:1", "rev-id:1", "profile x").to_string(),
        "27e082dc-6320-5c92-85c9-04f549c63f62"
    );
    assert_eq!(
        reparse_session_id(
            "demo",
            "source:1",
            "rev-id:1",
            "profile x",
            &parsed_region,
            Some("provider"),
            Some("model"),
            Some("model-v"),
            Some("prompt-v"),
            Some("parser-v")
        )
        .to_string(),
        "627f7a95-37e3-559c-a8ec-e3718985648b"
    );
    assert_eq!(
        generation_member_id("gen:1", "commit:1", 2).to_string(),
        "a311b04e-79bb-5b8f-9a99-51566b8b969f"
    );
    assert_eq!(
        frontier_id("session:1", &parsed_region, 3).to_string(),
        "30bcb063-9f72-5835-8024-26ed570c012b"
    );
    assert_eq!(
        legacy_generation_id("demo", "source:1").to_string(),
        "939f24df-a2fe-5baa-a0f5-246de751f2c9"
    );
}

#[test]
fn parse_target_matches_python_wire_contract() {
    assert_eq!(
        serde_json::to_value(target()).unwrap(),
        json!({
            "source_document_id": "source:1",
            "source_revision_id": "rev-id:1",
            "revision_document_id": "rev:1",
            "region": {
                "source_document_id": "rev:1",
                "start_char": 10,
                "end_char": 20
            },
            "reason": "reparse",
            "parser_profile": "profile x"
        })
    );
}

#[test]
fn region_and_target_validate_python_invariants() {
    assert!(matches!(
        SourceRegion::new("rev:1", 10, 10).unwrap_err(),
        ParseContractError::InvalidRegion(_)
    ));
    let mismatched_region = SourceRegion::new("source:1", 10, 20).unwrap();
    assert!(matches!(
        ParseTarget::new(
            "source:1",
            "rev-id:1",
            "rev:1",
            mismatched_region,
            "reparse",
            "profile x",
            None,
            None,
            None,
            None,
            None,
            None
        )
        .unwrap_err(),
        ParseContractError::InvalidTarget(_)
    ));
}
