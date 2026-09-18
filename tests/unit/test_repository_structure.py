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
            "cli_commands.py",
            "codex_compose_tui.py",
            "codex_memory.py",
            "codex_workbench_agent.py",
        },
        "diagnostics": {
            "debug_helpers.py",
        },
        "compose": {
            "options.py",
            "rendering.py",
            "validation.py",
        },
        "configuration": {
            "identity.py",
            "settings_service.py",
            "workspace.py",
        },
        "disambiguation": {
            "disambiguation_contracts.py",
            "reconciliation.py",
            "selection.py",
            "service.py",
        },
        "daemons": {
            "maintenance_budget.py",
            "projection_daemon.py",
            "maintenance_daemon.py",
            "runtime_support.py",
        },
        "cli": {
            "argument_parser.py",
            "archive_commands.py",
            "compose_commands.py",
            "content_commands.py",
            "entrypoint_support.py",
            "embedding_commands.py",
            "server_commands.py",
        },
        "agent": {
            "gateway_protocol.py",
            "gateway.py",
            "gateway_source.py",
            "maintenance_tools.py",
            "mcp_server.py",
            "protocol.py",
            "protocol_adapters.py",
            "read_tools.py",
            "source_tools.py",
            "tool_catalog.py",
        },
        "archiving": {
            "archive_contracts.py",
            "io.py",
            "operations.py",
            "validation.py",
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
        "ingest": {
            "artifacts.py",
            "base_kg_projection.py",
            "engine_builders.py",
            "graph_persistence.py",
            "maintenance_requests.py",
            "multimodal.py",
            "pipeline_run.py",
            "projection_access.py",
            "source_lifecycle.py",
            "source_parsing.py",
            "workbench_access.py",
        },
        "maintenance": {
            "dependency_planning.py",
            "job_dispatch.py",
            "worker_derived.py",
            "worker_execution.py",
            "worker_parse.py",
            "worker_runtime.py",
            "worker_selection.py",
            "state.py",
        },
        "parsing": {
            "layered_workflow.py",
            "longrun_support.py",
            "longrun_child.py",
            "parse_comparison.py",
            "parse_generation_store.py",
            "parse_quality.py",
            "parse_reconciliation.py",
            "parse_statistics.py",
            "parse_session_store.py",
            "parse_views.py",
        },
        "providers": {
            "role_config.py",
        },
        "projections": {
            "snapshot.py",
            "worker_impl.py",
        },
        "policies": {
            "rules.py",
        },
        "seeding": {
            "bundle_models.py",
            "operations.py",
        },
        "usage": {
            "aggregation.py",
            "events.py",
            "provider.py",
            "projection_engine.py",
            "usage_models.py",
        },
        "workbench": {
            "inspection.py",
            "investigation_history.py",
            "query.py",
            "review_query.py",
            "semantic_lens.py",
            "workbench.py",
            "workbench_api.py",
            "workbench_background.py",
            "workbench_cockpit.py",
            "workbench_http.py",
        },
    }

    for package, filenames in expected.items():
        assert {path.name for path in (root / package).glob("*.py")} >= filenames
        assert not any((root / filename).exists() for filename in filenames)


def test_root_has_no_removed_functional_facades() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "kogwistar_llm_wiki"
    removed_facades = {
        "agent_gateway.py",
        "archive.py",
        "compose_config.py",
        "contracts.py",
        "debug_run.py",
        "dependency_invalidation.py",
        "disambiguation_selection.py",
        "disambiguation_service.py",
        "entity_disambiguation.py",
        "graph_seed_bundle.py",
        "llm_usage.py",
        "mcp_agent_server.py",
        "model_catalog.py",
        "namespaces.py",
        "policies.py",
        "projection_worker.py",
        "provider_config.py",
        "settings.py",
        "usage_projection.py",
        "worker_state.py",
    }
    assert not {name for name in removed_facades if (root / name).exists()}
