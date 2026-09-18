"""Durable maintenance budget and background-selection policy.

The daemon owns lifecycle and polling; this mixin owns persistent budget,
profile-ladder, and background-cycle decisions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping
from typing import Any

from ..configuration.workspace import WorkspaceNamespaces
from ..maintenance import (
    MaintenanceProfileLadderDecision,
    add_usage,
    budget_fits,
    configured_maintenance_budget,
    configured_prices,
    configured_profile_ladder,
    configured_token_budget_rate,
    next_window_reset,
    reset_expired_spend,
    resolve_profile_ladder,
    select_embedding_exploration,
)
from ..maintenance.maintenance_control import MaintenanceControlState
from ..provider_config import resolve_maintenance_provider_settings
from ..utils import _temporary_namespace

logger = logging.getLogger(__name__)


class MaintenanceBudgetMixin:
    def _load_background_state(self) -> dict[str, Any]:
        path = getattr(self, "_background_state_path", None)
        if path is None:
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _load_budget_state(self) -> dict[str, Any]:
        path = getattr(self, "_budget_state_path", None)
        if path is None:
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _effective_budget(self, state: MaintenanceControlState) -> dict[str, dict[str, float]]:
        return state.budget or configured_maintenance_budget()

    def _effective_profile_ladder(self, state: MaintenanceControlState) -> list[dict[str, object]]:
        if state.profile_ladder_configured or state.profile_ladder:
            return state.profile_ladder
        return [
            {
                "name": level.name,
                "provider": level.provider,
                "model": level.model,
                "profile": level.profile,
                "budget": level.budget,
            }
            for level in configured_profile_ladder()
        ]

    @staticmethod
    def _profile_estimate() -> dict[str, float]:
        return {"tokens": 1000.0, "input_tokens": 800.0, "output_tokens": 200.0, "money": 0.0}

    def _select_profile_level(self, state: MaintenanceControlState) -> MaintenanceProfileLadderDecision:
        """Select the first affordable level; always rechecks primary for recovery."""
        decision = resolve_profile_ladder(
            enabled=state.enabled,
            requested=state.profile,
            budget=self._effective_budget(state),
            ladder=self._effective_profile_ladder(state),
            ladder_spend=self._budget_state.get("ladder_spend", {}),
            estimate=self._profile_estimate(),
        )
        self._last_profile_reason = decision.reason
        self._active_profile_level = decision.level.name if decision.level else None
        if decision.level is not None:
            self.provider_settings = resolve_maintenance_provider_settings(
                provider=decision.level.provider,
                model=decision.level.model,
                include_provider_chain=False,
            )
            self._worker.provider_settings = self.provider_settings
        return decision

    def _persist_budget_state(self) -> None:
        path = getattr(self, "_budget_state_path", None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._budget_state, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def profile_status(self, state: MaintenanceControlState | None = None) -> dict[str, object]:
        """Return redaction-free operational state for local diagnostics."""
        state = state or self.control_state
        ladder_decision = resolve_profile_ladder(
            enabled=state.enabled,
            requested=state.profile,
            budget=self._effective_budget(state),
            ladder=self._effective_profile_ladder(state),
            ladder_spend=getattr(self, "_budget_state", {}).get("ladder_spend", {}),
            estimate=self._profile_estimate(),
        )
        decision = ladder_decision.profile
        budget_state = getattr(self, "_budget_state", {})
        return {
            "enabled": state.enabled,
            "requested_profile": decision.requested,
            "effective_profile": decision.effective,
            "profile_reason": self._last_profile_reason if self._last_profile_reason != "configured" else decision.reason,
            "request_enabled": state.request_enabled,
            "background_enabled": state.background_enabled,
            "budget": self._effective_budget(state),
            "spend": budget_state.get("spend", {}) or state.spend,
            "next_window_reset": {
                window: next_window_reset(
                    float(budget_state.get("window_started_at", {}).get(window, time.time())),
                    window,
                    mode=os.getenv("LLM_WIKI_MAINTENANCE_BUDGET_WINDOW_MODE", "rolling").strip().lower(),
                )
                for window in self._effective_budget(state)
            },
            "deferred_cycle": state.deferred_cycle,
            "budget_window_mode": os.getenv("LLM_WIKI_MAINTENANCE_BUDGET_WINDOW_MODE", "rolling"),
            "poll_interval_seconds": self._current_poll_interval(),
            "profile_ladder": self._effective_profile_ladder(state),
            "active_profile_level": self._active_profile_level,
            "ladder_spend": budget_state.get("ladder_spend", {}),
        }

    def _current_poll_interval(self) -> float:
        streak = int(getattr(self, "_empty_poll_streak", 0))
        return min(float(self.poll_interval) * (2 ** min(streak, 5)), 300.0)

    def _record_profile_usage(self, attempt_id: str, usage: Mapping[str, float]) -> None:
        """Reconcile measured worker usage into durable window spend."""
        if attempt_id in self._recorded_usage_attempts:
            return
        self._recorded_usage_attempts.add(attempt_id)
        recorded_ids = list(self._budget_state.get("usage_attempt_ids") or [])
        recorded_ids.append(attempt_id)
        self._budget_state["usage_attempt_ids"] = recorded_ids[-2048:]
        self._refresh_budget_state(time.time())
        current_spend = self._budget_state.get("spend", {}) or self.control_state.spend
        updated_spend = add_usage(current_spend, usage)
        accumulated = float(self._budget_state.get("accumulated_call_tokens", 0) or 0)
        accumulated += float(usage.get("tokens", 0) or 0)
        self._budget_state.update({"spend": updated_spend, "accumulated_call_tokens": accumulated})
        active_profile_level = getattr(self, "_active_profile_level", None)
        if active_profile_level:
            ladder_spend = self._budget_state.setdefault("ladder_spend", {})
            level_spend = ladder_spend.get(active_profile_level, {})
            ladder_spend[active_profile_level] = add_usage(level_spend, usage)
            ladder_started = self._budget_state.setdefault("ladder_window_started_at", {})
            level_started = ladder_started.setdefault(active_profile_level, {})
            now = time.time()
            for window in ladder_spend[active_profile_level]:
                if isinstance(level_started, dict):
                    level_started.setdefault(window, now)
        self._persist_budget_state()
        if self.control is not None:
            self.control.update(spend=updated_spend, actor="maintenance-worker")

    def _refresh_budget_state(self, now: float) -> None:
        mode = os.getenv("LLM_WIKI_MAINTENANCE_BUDGET_WINDOW_MODE", "rolling").strip().lower()
        budget_state = getattr(self, "_budget_state", {})
        spend, starts = reset_expired_spend(
            budget_state.get("spend", {}),
            budget_state.get("window_started_at", {}),
            now=now,
            mode=mode,
        )
        budget_state["spend"] = spend
        budget_state["window_started_at"] = starts
        self._budget_state = budget_state
        ladder_spend = budget_state.get("ladder_spend", {})
        ladder_started = budget_state.get("ladder_window_started_at", {})
        if isinstance(ladder_spend, Mapping) and isinstance(ladder_started, Mapping):
            refreshed_ladder: dict[str, object] = {}
            refreshed_starts: dict[str, object] = {}
            for level_name, spend in ladder_spend.items():
                if not isinstance(spend, Mapping):
                    continue
                starts = ladder_started.get(level_name, {})
                if not isinstance(starts, Mapping):
                    starts = {}
                refreshed, starts = reset_expired_spend(spend, starts, now=now, mode=mode)
                refreshed_ladder[str(level_name)] = refreshed
                refreshed_starts[str(level_name)] = starts
            budget_state["ladder_spend"] = refreshed_ladder
            budget_state["ladder_window_started_at"] = refreshed_starts
        self._persist_budget_state()

    def _queue_has_work(self) -> bool:
        try:
            jobs = self.engines.conversation.jobs
            namespace = WorkspaceNamespaces(self.workspace_id).maintenance_jobs
            return bool(jobs.list(namespace=namespace, status="PENDING", limit=1))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return True

    def _persist_background_state(
        self,
        *,
        cycle_number: int,
        cycle_seed: int,
        selected_ids: set[str],
        now_ms: int,
    ) -> None:
        path = getattr(self, "_background_state_path", None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cycle_number": int(cycle_number),
            "cycle_seed": int(cycle_seed),
            "last_cycle_at_ms": int(now_ms),
            "recent_selection_watermark": int(now_ms),
            "recent_candidate_ids": sorted(selected_ids),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _schedule_background_cycle(self, state: MaintenanceControlState) -> None:
        """Queue one bounded, auditable background pass when the LLM is free."""
        now_ms = int(time.time() * 1000)
        self._refresh_budget_state(now_ms / 1000.0)
        last_cycle_at_ms = int(getattr(self, "_last_background_cycle_at_ms", 0) or 0)
        ladder_decision = self._select_profile_level(state)
        decision = ladder_decision.profile
        self._last_profile_reason = decision.reason
        if not state.background_enabled or decision.effective == "off" or (
            not self._effective_profile_ladder(state) and decision.effective == "lite"
        ) or (
            last_cycle_at_ms and now_ms - last_cycle_at_ms < self.background_interval * 1000
        ):
            return
        selected_budget = (
            ladder_decision.level.budget
            if ladder_decision.level is not None and ladder_decision.level.budget
            else self._effective_budget(state)
        )
        if decision.effective == "budgeted":
            if float(self._budget_state.get("accumulated_call_tokens", 0) or 0) < configured_token_budget_rate():
                self._last_profile_reason = "waiting_for_token_budget_rate"
                return
            if any(
                "money" in metrics
                for metrics in selected_budget.values()
            ) and os.getenv("LLM_WIKI_MAINTENANCE_MODEL_CLASS", "unknown").strip().lower() != "free":
                prices = configured_prices()
                if prices["input"] is None or prices["output"] is None:
                    self._last_profile_reason = "deferred:money_price_unavailable"
                    return
            prices = configured_prices()
            estimated_money = 0.0
            if prices["input"] is not None and prices["output"] is not None:
                estimated_money = (
                    800.0 * prices["input"] + 200.0 * prices["output"]
                ) / 1_000_000.0
            estimate = {
                "tokens": 1000.0,
                "input_tokens": 800.0,
                "output_tokens": 200.0,
                "money": estimated_money,
            }
            fits, reason = budget_fits(
                selected_budget,
                (
                    self._budget_state.get("ladder_spend", {}).get(ladder_decision.level.name, {})
                    if ladder_decision.level is not None
                    else self._budget_state.get("spend", {}) or state.spend
                ),
                estimate,
            )
            if not fits:
                self._last_profile_reason = f"deferred:{reason}"
                return
        jobs = self.engines.conversation.jobs
        active = jobs.list(namespace=WorkspaceNamespaces(self.workspace_id).maintenance_jobs, status="DOING", limit=50)
        queued = jobs.list(namespace=WorkspaceNamespaces(self.workspace_id).maintenance_jobs, status="PENDING", limit=50)
        if active or any(
            str(getattr(job, "payload", {}).get("mode") or "request") != "background"
            for job in queued
        ):
            return
        cycle_number = int(getattr(self, "_cycle_number", 0)) + 1
        seed_material = f"{self.workspace_id}:{cycle_number}".encode()
        cycle_seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        ns = WorkspaceNamespaces(self.workspace_id)
        try:
            with _temporary_namespace(self.engines.kg, ns.curated_kg_space):
                nodes = self.engines.kg.read.get_nodes(limit=500)
        except Exception as exc:  # noqa: BLE001 - backend failures degrade exploration only
            logger.warning("Background maintenance selection degraded: %s", exc)
            nodes = []
        scoped_nodes = []
        for node in nodes:
            metadata = getattr(node, "metadata", {})
            declared_workspace = (
                str(metadata.get("workspace_id") or "").strip()
                if isinstance(metadata, Mapping)
                else ""
            )
            if not declared_workspace or declared_workspace == self.workspace_id:
                scoped_nodes.append(node)
        nodes = scoped_nodes
        recent = sorted(
            (node for node in nodes if getattr(node, "safe_get_id", lambda: "")()),
            key=lambda node: str(getattr(node, "metadata", {}).get("updated_at_ms", "")),
            reverse=True,
        )[:6]
        explored, strategy = select_embedding_exploration(
            nodes,
            dimension=next(
                (
                    len(getattr(node, "embedding", []))
                    for node in nodes
                    if getattr(node, "embedding", None)
                ),
                1,
            ),
            cycle_seed=cycle_seed,
            max_candidates=6,
            excluded_ids={str(node.safe_get_id()) for node in recent}
            | set(getattr(self, "_recent_background_ids", set())),
        )
        selected = [
            {"candidate_id": str(node.safe_get_id()), "reason": "recent_interest", "score": None}
            for node in recent
        ] + [item.as_dict() for item in explored]
        payload = {
            "workspace_id": self.workspace_id,
            "maintenance_kind": "distill",
            "mode": "background",
            "maintenance_origin": "background",
            "selection_strategy": "recent_interest_and_embedding_probe",
            "embedding_exploration": {
                "profile": os.environ.get("KOGWISTAR_LLM_WIKI_EMBED_PROFILE", "unknown"),
                "dimension": len(getattr(nodes[0], "embedding", []) or []) if nodes else None,
                "probe_seed": cycle_seed,
                "strategy": strategy,
                "candidates": [item.as_dict() for item in explored],
            },
            "candidates": selected,
            "candidate_exclusions": sorted({str(node.safe_get_id()) for node in recent}),
            "recent_selection_watermark": int(time.time() * 1000),
            "stop_reason": None,
            "maintenance_round": 0,
            "maintenance_max_rounds": 1,
            "budgets": {"max_steps": 1},
        }
        selected_ids = {
            str(item["candidate_id"])
            for item in selected
            if str(item.get("candidate_id") or "").strip()
        }
        # Reserve the cycle before queueing. A crash may skip a cycle, but it
        # can never reuse a previously committed seed or job identity.
        self._persist_background_state(
            cycle_number=cycle_number,
            cycle_seed=cycle_seed,
            selected_ids=selected_ids,
            now_ms=now_ms,
        )
        self._cycle_number = cycle_number
        self._last_background_cycle_at_ms = now_ms
        self._recent_background_ids = selected_ids
        job_id = f"background-maintenance:{self.workspace_id}:{cycle_number}"
        jobs.enqueue(
            job_id=job_id,
            namespace=ns.maintenance_jobs,
            entity_kind="maintenance_cycle",
            entity_id=job_id,
            job_kind="maintenance_job:distill",
            payload=payload,
            max_retries=1,
        )
        if decision.effective == "budgeted":
            self._budget_state["accumulated_call_tokens"] = max(
                0.0,
                float(self._budget_state.get("accumulated_call_tokens", 0) or 0)
                - configured_token_budget_rate(),
            )
            self._persist_budget_state()
        self._worker._emit_trace(
            "maintenance_background_cycle_scheduled",
            workspace_id=self.workspace_id,
            cycle_number=cycle_number,
            cycle_seed=cycle_seed,
            selected_count=len(selected),
            exploration_strategy=strategy,
            maintenance_profile=decision.effective,
        )
