from dataclasses import replace
from pathlib import Path


def test_sync_promotion_mode_controls_promotion(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    assert artifacts.promoted_entity_id is None

    sync_request = replace(ingest_request, promotion_mode="sync")
    artifacts_sync = pipeline.run(sync_request)
    assert artifacts_sync.promoted_entity_id is not None

    req_high = replace(sync_request, auto_accept_threshold=0.96)
    artifacts_high = pipeline.run(req_high)
    assert artifacts_high.promoted_entity_id is None

def test_sync_obsidian_vault_uses_updated_counters(monkeypatch, pipeline, tmp_path):
    vault_dir = tmp_path / "obsidian_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

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
