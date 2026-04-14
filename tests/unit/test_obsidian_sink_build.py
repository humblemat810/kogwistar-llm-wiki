from pathlib import Path

from kogwistar_llm_wiki import IngestPipeline


def test_build_obsidian_vault_materializes_expected_files(pipeline, ingest_request, tmp_path: Path):
    pipeline = pipeline
    pipeline.run(ingest_request)

    result = pipeline.build_obsidian_vault(tmp_path / "vault", workspace_id=ingest_request.workspace_id)

    assert result.notes >= 1
    assert result.canvases >= 1
    assert (tmp_path / "vault" / "System" / "ledger.json").exists()
    assert (tmp_path / "vault" / "System" / "index.md").exists()
    assert (tmp_path / "vault" / "Views").exists()
