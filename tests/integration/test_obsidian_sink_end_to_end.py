def test_obsidian_sink_builds_vault_from_promoted_kg_state(pipeline, ingest_request, tmp_path):
    artifacts = pipeline.run(ingest_request.model_copy(update={"promotion_mode": "sync"}))
    assert artifacts.promoted_entity_id is not None

    result = pipeline.build_obsidian_vault(tmp_path / "vault", workspace_id=ingest_request.workspace_id)

    assert result.notes >= 1
    concepts = list((tmp_path / "vault").rglob("*.md"))
    assert any(path.name == "System" for path in [p.parent for p in concepts]) or (tmp_path / "vault" / "System" / "index.md").exists()
    assert any(path.stem == ingest_request.title for path in concepts if path.name != "index.md")
