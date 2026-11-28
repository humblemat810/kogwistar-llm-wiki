use kogwistar_llm_wiki_port::ids::stable_id;

#[test]
fn stable_ids_match_python_ascii_and_non_ascii_goldens() {
    assert_eq!(
        stable_id(
            "kogwistar_llm_wiki.source_document",
            &["demo", "file:///x.txt"]
        )
        .to_string(),
        "ee71427f-2f63-5750-8623-329f0d9e7854"
    );
    assert_eq!(
        stable_id(
            "kogwistar_llm_wiki.source_document",
            &["演示", "file:///測試.txt"]
        )
        .to_string(),
        "ca3933f5-6a00-5426-a754-52047e7f32f6"
    );
}
