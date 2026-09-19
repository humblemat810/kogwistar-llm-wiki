from __future__ import annotations

import copy
import pickle
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from kogwistar_llm_wiki.codex.codex_compose_tui import LaunchStep, TuiConfiguration
from kogwistar_llm_wiki.codex.codex_bridge import CodexBridgeState
from kogwistar_llm_wiki.codex.codex_workbench_agent import CodexCliSettings
from kogwistar_llm_wiki.codex.codex_memory import CodexMemoryService
from kogwistar_llm_wiki.compose.options import ComposeOptions
from kogwistar_llm_wiki.configuration.identity import LlmWikiIdentity
from kogwistar_llm_wiki.configuration.settings_service import SettingsService
from kogwistar_llm_wiki.diagnostics.debug_helpers import LiveTracePrinter
from kogwistar_llm_wiki.embeddings.multimodal_sources import MappingAssetResolver
from kogwistar_llm_wiki.maintenance.maintenance_strategies import (
    MaintenanceJobExecutionContext,
    MaintenanceStrategyRegistry,
)
from kogwistar_llm_wiki.parsing.parse_session_store import ParseSessionStore
from kogwistar_llm_wiki.parsing.parse_statistics import ParseStatisticsStore
from kogwistar_llm_wiki.parsing.parse_views import ParseViewResolver, ParseViewStore
from kogwistar_llm_wiki.disambiguation.service import DisambiguationService
from kogwistar_llm_wiki.embeddings.multimodal_grounding import EvidenceClosureValidator
from kogwistar_llm_wiki.workbench.investigation_history import InvestigationHistoryService
from kogwistar_llm_wiki.workbench.query import GraphSpaceQueryService
from kogwistar_llm_wiki.workbench.review_query import ReviewQueryService
from kogwistar_llm_wiki.workbench.semantic_lens import SemanticLensService
from kogwistar_llm_wiki.workbench.workbench_background import WorkbenchInteractionStore


@pytest.mark.ci
def test_wave_one_value_objects_use_strict_slots() -> None:
    values = (
        ComposeOptions(),
        LaunchStep(description="check", command=["echo", "ok"]),
        TuiConfiguration(),
        LlmWikiIdentity(
            principal_id="test",
            scopes=frozenset({"read"}),
            role="ro",
            security_scope="test",
            workspaces=frozenset({"demo"}),
            claims={},
            auth_mode="disabled",
        ),
        MaintenanceJobExecutionContext(
            workspace_id="demo",
            job=object(),
            job_id="job-1",
            payload={},
            request_node=None,
            request_node_id="node-1",
            lane_message_id="message-1",
            maintenance_kind="maintenance",
        ),
    )

    for value in values:
        assert not hasattr(value, "__dict__")
        assert "__dict__" not in value.__slots__


@pytest.mark.ci
def test_strict_slots_preserve_frozen_dataclass_behavior() -> None:
    with pytest.raises(FrozenInstanceError):
        ComposeOptions().backend = "chroma"  # type: ignore[misc]


@pytest.mark.ci
@pytest.mark.parametrize(
    "value",
    [
        ComposeOptions(),
        LaunchStep(description="check", command=["echo", "ok"]),
        TuiConfiguration(),
        LlmWikiIdentity(
            principal_id="test",
            scopes=frozenset({"read"}),
            role="ro",
            security_scope="test",
            workspaces=frozenset({"demo"}),
            claims={"tenant": "demo"},
            auth_mode="disabled",
        ),
    ],
)
def test_slotted_value_objects_preserve_copy_pickle_and_dataclass_contracts(value) -> None:
    assert copy.copy(value) == value
    assert copy.deepcopy(value) == value
    assert pickle.loads(pickle.dumps(value)) == value
    assert asdict(value) == asdict(replace(value))


@pytest.mark.ci
def test_fixed_state_services_use_strict_slots(tmp_path) -> None:
    values = (
        ParseStatisticsStore(tmp_path / "stats.sqlite3"),
        ParseSessionStore(object(), workspace_id="demo"),
        ParseViewStore(object(), workspace_id="demo"),
        ParseViewResolver(object(), workspace_id="demo"),
        InvestigationHistoryService(object()),
        GraphSpaceQueryService(object()),
        DisambiguationService(object()),
        EvidenceClosureValidator(object()),
        SettingsService(object()),
        CodexBridgeState(token="secret", settings=CodexCliSettings()),
        CodexMemoryService(object(), enabled=False),
        SemanticLensService(object(), query_service=object()),
        ReviewQueryService(object()),
        WorkbenchInteractionStore(object()),
        MaintenanceStrategyRegistry(),
        LiveTracePrinter(),
        MappingAssetResolver({}),
    )

    for value in values:
        assert not hasattr(value, "__dict__")
