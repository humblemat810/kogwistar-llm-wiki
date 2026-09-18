from pathlib import Path

from kogwistar_llm_wiki import maintenance
from kogwistar_llm_wiki.codex import CodexMemoryRecord
from kogwistar_llm_wiki.embeddings import VllmEmbeddingSettings
from kogwistar_llm_wiki.maintenance.maintenance_policy import normalize_maintenance_kind
from kogwistar_llm_wiki.maintenance.maintenance_profiles import normalize_profile_ladder
from kogwistar_llm_wiki.parsing import ParseTarget


def test_maintenance_domain_facade_preserves_legacy_module_contracts() -> None:
    assert maintenance.normalize_maintenance_kind is normalize_maintenance_kind
    assert maintenance.normalize_profile_ladder is normalize_profile_ladder


def test_bounded_context_facades_expose_existing_public_contracts() -> None:
    assert CodexMemoryRecord.__module__.endswith("codex_memory")
    assert VllmEmbeddingSettings.__module__.endswith("vllm_remote")
    assert ParseTarget.__module__.endswith("parse_views")
    assert maintenance.select_request_candidates is not None
    assert normalize_maintenance_kind.__module__.startswith(
        "kogwistar_llm_wiki.maintenance."
    )


def test_functional_implementations_live_inside_their_owning_packages() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "kogwistar_llm_wiki"
    expected = {
        "codex": {
            "codex_bridge.py",
            "codex_compose_tui.py",
            "codex_memory.py",
            "codex_workbench_agent.py",
        },
        "embeddings": {
            "embedding_config_resolver.py",
            "multimodal_grounding.py",
            "multimodal_projection.py",
            "multimodal_remote.py",
            "multimodal_runtime.py",
            "multimodal_sources.py",
            "vllm_remote.py",
        },
        "parsing": {
            "parse_comparison.py",
            "parse_generation_store.py",
            "parse_reconciliation.py",
            "parse_session_store.py",
            "parse_views.py",
        },
    }

    for package, filenames in expected.items():
        assert {path.name for path in (root / package).glob("*.py")} >= filenames
        assert not any((root / filename).exists() for filename in filenames)
