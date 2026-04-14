from pathlib import Path


def test_auto_accept_threshold_controls_promotion(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    assert artifacts.promoted_entity_id is not None

    req_high = type(ingest_request)(
        workspace_id=ingest_request.workspace_id,
        source_uri=ingest_request.source_uri,
        title=ingest_request.title,
        raw_text=ingest_request.raw_text,
        source_format=ingest_request.source_format,
        parser_mode=ingest_request.parser_mode,
        auto_accept_threshold=0.96,
    )
    artifacts_high = pipeline.run(req_high)
    assert artifacts_high.promoted_entity_id is None

def test_sync_obsidian_vault_uses_updated_counters(tmp_path_factory, monkeypatch, pipeline, tmp_path):
    vault_dir = tmp_path_factory.mktemp("obsidian_vault")

    class FakeSink:
        def __init__(self, vault_root):
            self.vault_root = vault_root

        def sync(self, provider, **kwargs):
            return {
                "updated_notes": 2,
                "updated_canvases": 1,
                "dangling_links": 0,
            }

    import kogwistar_llm_wiki.ingest_pipeline as mod

    monkeypatch.setattr(mod, "ObsidianVaultSink", FakeSink)
    monkeypatch.setattr(mod, "KogwistarDuckProvider", lambda *_args, **_kwargs: object())

    result = pipeline.sync_obsidian_vault(Path(vault_dir))

    assert result.notes == 2
    assert result.canvases == 1
    assert result.dangling_links == 0
