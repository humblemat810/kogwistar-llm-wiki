from __future__ import annotations

import pytest

from kogwistar_llm_wiki.maintenance.maintenance_control import (
    MaintenanceControl,
    send_control_command,
)
from kogwistar_llm_wiki.maintenance.maintenance_profiles import (
    budget_fits,
    configured_prices,
    next_window_reset,
    normalize_budget,
    normalize_profile_ladder,
    reset_expired_spend,
    resolve_profile,
    resolve_profile_ladder,
)


def test_new_control_state_is_master_disabled_from_deployment_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_WIKI_MAINTENANCE_ENABLED", raising=False)
    state = MaintenanceControl(tmp_path).get()
    assert state.enabled is False
    assert state.request_enabled is True
    assert state.background_enabled is False


def test_control_state_reads_independent_switch_defaults_from_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_MAINTENANCE_REQUEST_ENABLED", "false")
    monkeypatch.setenv("LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED", "true")
    state = MaintenanceControl(tmp_path).get()
    assert state.request_enabled is False
    assert state.background_enabled is True


def test_budgeted_profile_fails_closed_without_a_budget() -> None:
    decision = resolve_profile(enabled=True, requested="budgeted", budget={})
    assert decision.effective == "lite"
    assert decision.reason == "budgeted_requires_configured_budget"


def test_profile_ladder_cascades_and_recovers_primary_after_reset() -> None:
    ladder = [
        {"name": "codex-high", "provider": "codex", "profile": "high", "budget": {"daily": {"tokens": 10}}},
        {"name": "ollama-balanced", "provider": "ollama", "profile": "balanced", "budget": {"daily": {"tokens": 100}}},
        {"name": "ollama-lite", "provider": "ollama", "profile": "lite", "budget": {}},
    ]
    first = resolve_profile_ladder(
        enabled=True, requested="high", budget={}, ladder=ladder,
        ladder_spend={}, estimate={"tokens": 5},
    )
    assert first.level is not None and first.level.name == "codex-high"
    second = resolve_profile_ladder(
        enabled=True, requested="high", budget={}, ladder=ladder,
        ladder_spend={"codex-high": {"daily": {"tokens": 10}}}, estimate={"tokens": 5},
    )
    assert second.level is not None and second.level.name == "ollama-balanced"
    recovered = resolve_profile_ladder(
        enabled=True, requested="high", budget={}, ladder=ladder,
        ladder_spend={}, estimate={"tokens": 5},
    )
    assert recovered.level is not None and recovered.level.name == "codex-high"


def test_profile_ladder_rejects_duplicate_names_and_bad_shape() -> None:
    with pytest.raises(ValueError, match="unique"):
        normalize_profile_ladder([
            {"name": "same", "provider": "codex"},
            {"name": "same", "provider": "ollama"},
        ])
    with pytest.raises(TypeError, match="JSON array"):
        normalize_profile_ladder({"name": "not-a-list"})


def test_profile_ladder_control_state_is_durable_and_can_be_cleared(tmp_path) -> None:
    ladder = [{"name": "primary", "provider": "codex", "profile": "high"}]
    control = MaintenanceControl(tmp_path)
    control.update(profile_ladder=ladder)
    restored = MaintenanceControl(tmp_path).get()
    assert restored.profile_ladder_configured is True
    assert restored.profile_ladder[0]["provider"] == "codex"
    control.update(profile_ladder=[])
    assert MaintenanceControl(tmp_path).get().profile_ladder == []


def test_profile_ladder_survives_socket_fallback(tmp_path, monkeypatch) -> None:
    from kogwistar_llm_wiki.maintenance import maintenance_control

    def unavailable_socket(*args, **kwargs):
        raise OSError("socket unavailable")

    monkeypatch.setattr(maintenance_control.socket, "socket", unavailable_socket)
    ladder = [{"name": "primary", "provider": "codex", "profile": "high"}]
    result = send_control_command(tmp_path, profile_ladder=ladder)

    assert result["ok"] is True
    assert result["profile_ladder"][0]["name"] == "primary"
    assert MaintenanceControl(tmp_path).get().profile_ladder[0]["provider"] == "codex"


def test_budget_caps_are_independent_and_checked_across_windows() -> None:
    budget = normalize_budget({"daily": {"output_tokens": 20, "money": 1.0}, "monthly": {"tokens": 100}})
    fits, reason = budget_fits(
        budget,
        {"daily": {"output_tokens": 19}, "monthly": {"tokens": 50}},
        {"output_tokens": 2, "tokens": 10, "money": 0.1},
    )
    assert fits is False
    assert reason == "daily_output_tokens_cap"


def test_invalid_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        normalize_budget({"daily": {"tokens": -1}})


def test_profile_and_budget_updates_are_durable_and_status_is_read_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_MAINTENANCE_ENABLED", "false")
    control = MaintenanceControl(tmp_path)
    state = control.update(
        enabled=True,
        profile="budgeted",
        budget={"daily": {"output_tokens": 20}},
    )
    assert state.enabled is True
    assert state.profile == "budgeted"
    assert state.budget == {"daily": {"output_tokens": 20.0}}

    status = send_control_command(tmp_path, status=True)
    assert status["ok"] is True
    assert status["profile"] == "budgeted"
    assert status["budget"] == {"daily": {"output_tokens": 20.0}}


def test_rolling_spend_resets_only_expired_windows() -> None:
    spend, starts = reset_expired_spend(
        {"daily": {"tokens": 4}, "monthly": {"tokens": 9}},
        {"daily": 0, "monthly": 86_000},
        now=86_401,
    )
    assert "daily" not in spend
    assert spend["monthly"]["tokens"] == 9
    assert starts["daily"] == 86_401


def test_price_table_is_optional_but_typed() -> None:
    assert configured_prices() == {"input": None, "output": None}


def test_calendar_window_status_reports_the_next_boundary() -> None:
    now = 1_735_689_600.0
    reset = next_window_reset(now, "monthly", mode="calendar")
    assert reset > now
    assert reset - now < 32 * 86_400
