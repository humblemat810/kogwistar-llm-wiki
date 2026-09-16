from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import kogwistar_llm_wiki.daemon as daemon_module
import kogwistar_llm_wiki.worker as worker_module
from kogwistar_llm_wiki.daemon import MaintenanceDaemon
from kogwistar_llm_wiki.maintenance_control import MaintenanceControlState
from kogwistar_llm_wiki.maintenance_strategies import MaintenanceJobExecutionContext
from kogwistar_llm_wiki.worker import MaintenanceWorker


class FakeNode:
    def __init__(self, node_id: str, vector: list[float] | None, **metadata: object) -> None:
        self.id = node_id
        self.embedding = vector
        self.metadata = metadata

    def safe_get_id(self) -> str:
        return self.id


def test_request_payload_is_enriched_from_fake_graph(monkeypatch) -> None:
    seed = FakeNode("seed", [1.0, 0.0])
    neighbor = FakeNode("neighbor", [0.0, 1.0])
    semantic = FakeNode("semantic", [0.9, 0.1])
    graph = SimpleNamespace(
        read=SimpleNamespace(
            get_nodes=lambda **_: [seed, neighbor, semantic],
            get_edges=lambda **_: [SimpleNamespace(source_ids=["seed"], target_ids=["neighbor"])],
        )
    )
    worker = object.__new__(MaintenanceWorker)
    worker.engines = SimpleNamespace(kg=graph)
    worker._emit_trace = lambda *_args, **_kwargs: None
    monkeypatch.setattr(worker_module, "_temporary_namespace", lambda *_args: nullcontext())
    ctx = MaintenanceJobExecutionContext(
        workspace_id="demo",
        job=SimpleNamespace(job_id="job-1"),
        job_id="job-1",
        payload={"mode": "request", "seed_node_ids": ["seed"]},
        request_node=None,
        request_node_id="seed",
        lane_message_id="",
        maintenance_kind="document_propose_crosslinks",
    )

    worker._attach_request_selection(ctx)

    assert [item["candidate_id"] for item in ctx.payload["maintenance_candidates"]] == [
        "neighbor",
        "semantic",
    ]
    assert ctx.payload["selection_strategy"] == "connected_semantic_evidence_history"


def test_background_cycle_enqueues_fake_payload_with_recent_and_probe_halves(monkeypatch) -> None:
    nodes = [
        FakeNode("recent", [1.0, 0.0], updated_at_ms=20),
        *[
            FakeNode(f"explore-{index}", [0.0, 1.0], updated_at_ms=10 - index)
            for index in range(7)
        ],
    ]
    enqueued: list[dict[str, object]] = []

    class FakeJobs:
        def list(self, **_kwargs):
            return []

        def enqueue(self, **kwargs):
            enqueued.append(kwargs)
            return str(kwargs["job_id"])

    graph = SimpleNamespace(read=SimpleNamespace(get_nodes=lambda **_: nodes))
    engines = SimpleNamespace(
        kg=graph,
        conversation=SimpleNamespace(jobs=FakeJobs()),
    )
    daemon = object.__new__(MaintenanceDaemon)
    daemon.engines = engines
    daemon.workspace_id = "demo"
    daemon.background_interval = 1.0
    daemon._last_background_cycle = 0.0
    daemon._cycle_number = 0
    daemon.control_state = MaintenanceControlState(background_enabled=True)
    daemon._worker = SimpleNamespace(_emit_trace=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daemon_module, "_temporary_namespace", lambda *_args: nullcontext())

    daemon._schedule_background_cycle(daemon.control_state)

    payload = enqueued[0]["payload"]
    assert payload["mode"] == "background"
    assert payload["embedding_exploration"]["strategy"] == "embedding_probe"
    assert payload["embedding_exploration"]["probe_seed"]
    assert any(item["reason"] == "recent_interest" for item in payload["candidates"])
    assert any(item["reason"] == "semantic_similar" for item in payload["candidates"])


def test_background_cycle_identity_and_exclusions_survive_restart(tmp_path, monkeypatch) -> None:
    nodes = [
        FakeNode("recent", [1.0, 0.0], updated_at_ms=20),
        *[
            FakeNode(f"explore-{index}", [0.0, 1.0], updated_at_ms=10 - index)
            for index in range(7)
        ],
    ]
    enqueued: list[dict[str, object]] = []

    class FakeJobs:
        def list(self, **_kwargs):
            return []

        def enqueue(self, **kwargs):
            enqueued.append(kwargs)
            return str(kwargs["job_id"])

    graph = SimpleNamespace(read=SimpleNamespace(get_nodes=lambda **_: nodes))
    engines = SimpleNamespace(
        kg=graph,
        conversation=SimpleNamespace(jobs=FakeJobs()),
    )
    monkeypatch.setattr(daemon_module, "_temporary_namespace", lambda *_args: nullcontext())

    def make_daemon() -> MaintenanceDaemon:
        daemon = object.__new__(MaintenanceDaemon)
        daemon.engines = engines
        daemon.workspace_id = "demo"
        daemon.background_interval = 1.0
        daemon._background_state_path = tmp_path / "maintenance" / "background_state.json"
        daemon._background_state = daemon._load_background_state()
        daemon._last_background_cycle_at_ms = 0
        daemon._cycle_number = int(daemon._background_state.get("cycle_number") or 0)
        daemon._recent_background_ids = set(daemon._background_state.get("recent_candidate_ids") or [])
        daemon._worker = SimpleNamespace(_emit_trace=lambda *_args, **_kwargs: None)
        return daemon

    first = make_daemon()
    first._schedule_background_cycle(MaintenanceControlState(background_enabled=True))
    second = make_daemon()
    second._last_background_cycle_at_ms = 0
    second._schedule_background_cycle(MaintenanceControlState(background_enabled=True))

    assert len(enqueued) == 2
    assert enqueued[0]["job_id"] != enqueued[1]["job_id"]
    assert enqueued[0]["payload"]["embedding_exploration"]["probe_seed"] != enqueued[1]["payload"]["embedding_exploration"]["probe_seed"]
    assert second._cycle_number == 2
    assert (tmp_path / "maintenance" / "background_state.json").exists()


def test_profile_usage_is_idempotent_after_daemon_restart(tmp_path) -> None:
    def make_daemon() -> MaintenanceDaemon:
        daemon = object.__new__(MaintenanceDaemon)
        daemon._budget_state_path = tmp_path / "maintenance" / "budget_state.json"
        daemon._budget_state = daemon._load_budget_state()
        daemon._recorded_usage_attempts = {
            str(item)
            for item in (daemon._budget_state.get("usage_attempt_ids") or [])
            if str(item).strip()
        }
        daemon.control = None
        daemon.control_state = MaintenanceControlState()
        return daemon

    first = make_daemon()
    first._record_profile_usage("attempt-1", {"tokens": 12, "input_tokens": 10, "output_tokens": 2})
    second = make_daemon()
    second._record_profile_usage("attempt-1", {"tokens": 12, "input_tokens": 10, "output_tokens": 2})

    state = json.loads((tmp_path / "maintenance" / "budget_state.json").read_text(encoding="utf-8"))
    assert state["spend"]["daily"]["tokens"] == 12
    assert state["usage_attempt_ids"] == ["attempt-1"]


def test_daemon_applies_selected_ladder_provider_without_global_chain(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_resolve(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(parser=SimpleNamespace(provider=kwargs["provider"]))

    daemon = object.__new__(MaintenanceDaemon)
    daemon._budget_state = {"ladder_spend": {"primary": {"daily": {"tokens": 100}}}}
    daemon._worker = SimpleNamespace(provider_settings=None)
    daemon.control_state = MaintenanceControlState(
        enabled=True,
        profile="high",
        profile_ladder=[
            {"name": "primary", "provider": "codex", "profile": "high", "budget": {"daily": {"tokens": 100}}},
            {"name": "fallback", "provider": "ollama", "profile": "balanced", "budget": {"daily": {"tokens": 2000}}},
        ],
        profile_ladder_configured=True,
    )
    monkeypatch.setattr(daemon_module, "resolve_maintenance_provider_settings", fake_resolve)

    decision = daemon._select_profile_level(daemon.control_state)

    assert decision.level is not None and decision.level.name == "fallback"
    assert calls == [{"provider": "ollama", "model": None, "include_provider_chain": False}]
    assert daemon._worker.provider_settings.parser.provider == "ollama"
