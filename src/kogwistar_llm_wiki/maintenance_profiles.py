"""Runtime maintenance profiles and windowed budget policy.

This module is intentionally provider-agnostic.  It decides whether a
background cycle may start; the worker remains responsible for measuring and
recording actual provider usage.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

PROFILE_NAMES = frozenset({"high", "balanced", "budgeted", "lite"})
WINDOW_NAMES = ("daily", "weekly", "monthly")
METRIC_NAMES = ("tokens", "input_tokens", "output_tokens", "money")
WINDOW_SECONDS = {"daily": 86_400, "weekly": 604_800, "monthly": 2_592_000}
MODEL_CLASSES = frozenset({"free", "metered", "unknown"})


def _optional_number(value: object, *, name: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative number or empty") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return result


def normalize_budget(value: Mapping[str, object] | None) -> dict[str, dict[str, float]]:
    """Validate and normalize a persisted or runtime budget mapping."""
    result: dict[str, dict[str, float]] = {}
    for window, raw_metrics in (value or {}).items():
        if window not in WINDOW_NAMES:
            raise ValueError(f"unknown maintenance budget window: {window}")
        if not isinstance(raw_metrics, Mapping):
            raise TypeError(f"maintenance budget {window} must be an object")
        metrics: dict[str, float] = {}
        for metric, raw_cap in raw_metrics.items():
            if metric not in METRIC_NAMES:
                raise ValueError(f"unknown maintenance budget metric: {metric}")
            cap = _optional_number(raw_cap, name=f"{window}.{metric}")
            if cap is not None:
                metrics[metric] = cap
        if metrics:
            result[window] = metrics
    return result


def configured_maintenance_enabled() -> bool:
    value = os.getenv("LLM_WIKI_MAINTENANCE_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def configured_maintenance_profile() -> str:
    requested = os.getenv("LLM_WIKI_MAINTENANCE_PROFILE", "").strip().lower()
    if requested:
        if requested not in PROFILE_NAMES:
            raise ValueError("LLM_WIKI_MAINTENANCE_PROFILE must be high, balanced, budgeted, or lite")
        return requested
    model_class = os.getenv("LLM_WIKI_MAINTENANCE_MODEL_CLASS", "unknown").strip().lower()
    if model_class not in MODEL_CLASSES:
        raise ValueError("LLM_WIKI_MAINTENANCE_MODEL_CLASS must be free, metered, or unknown")
    return "high" if model_class == "free" else "lite"


def configured_maintenance_budget() -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for window in WINDOW_NAMES:
        metrics: dict[str, float] = {}
        for metric in METRIC_NAMES:
            env_name = f"LLM_WIKI_MAINTENANCE_BUDGET_{window.upper()}_{metric.upper()}"
            cap = _optional_number(os.getenv(env_name), name=env_name)
            if cap is not None:
                metrics[metric] = cap
        if metrics:
            result[window] = metrics
    return result


def configured_token_budget_rate() -> int:
    raw = os.getenv("LLM_WIKI_MAINTENANCE_TOKEN_BUDGET_RATE", "100000").strip()
    try:
        result = int(raw)
    except ValueError as exc:
        raise ValueError("LLM_WIKI_MAINTENANCE_TOKEN_BUDGET_RATE must be a positive integer") from exc
    if result <= 0:
        raise ValueError("LLM_WIKI_MAINTENANCE_TOKEN_BUDGET_RATE must be positive")
    return result


def configured_prices() -> dict[str, float | None]:
    return {
        "input": _optional_number(
            os.getenv("LLM_WIKI_MAINTENANCE_PRICE_INPUT_PER_1M"),
            name="LLM_WIKI_MAINTENANCE_PRICE_INPUT_PER_1M",
        ),
        "output": _optional_number(
            os.getenv("LLM_WIKI_MAINTENANCE_PRICE_OUTPUT_PER_1M"),
            name="LLM_WIKI_MAINTENANCE_PRICE_OUTPUT_PER_1M",
        ),
    }


@dataclass(frozen=True, slots=True)
class MaintenanceProfileDecision:
    requested: str
    effective: str
    enabled: bool
    reason: str


@dataclass(frozen=True, slots=True)
class MaintenanceProfileLevel:
    """One optional ordered provider/profile level in a quota ladder."""

    name: str
    provider: str
    model: str | None
    profile: str
    budget: dict[str, dict[str, float]]


@dataclass(frozen=True, slots=True)
class MaintenanceProfileLadderDecision:
    """The first usable level, or a fail-closed exhausted result."""

    level: MaintenanceProfileLevel | None
    profile: MaintenanceProfileDecision
    level_index: int | None
    reason: str


def resolve_profile(
    *,
    enabled: bool,
    requested: str,
    budget: Mapping[str, object] | None,
) -> MaintenanceProfileDecision:
    requested = str(requested).strip().lower()
    if requested not in PROFILE_NAMES:
        raise ValueError(f"unknown maintenance profile: {requested}")
    if not enabled:
        return MaintenanceProfileDecision(requested, "off", False, "master_disabled")
    if requested == "budgeted" and not normalize_budget(budget):
        return MaintenanceProfileDecision(requested, "lite", True, "budgeted_requires_configured_budget")
    return MaintenanceProfileDecision(requested, requested, True, "configured")


def budget_fits(
    budget: Mapping[str, Mapping[str, object]],
    spend: Mapping[str, Mapping[str, object]],
    estimate: Mapping[str, float],
) -> tuple[bool, str]:
    """Check every configured metric in every active window."""
    normalized = normalize_budget(budget)
    for window, metrics in normalized.items():
        current = spend.get(window, {})
        for metric, cap in metrics.items():
            used = float(current.get(metric, 0.0) or 0.0)
            amount = float(estimate.get(metric, 0.0) or 0.0)
            if used + amount > cap:
                return False, f"{window}_{metric}_cap"
    return True, "within_budget"


def normalize_profile_ladder(value: object) -> list[MaintenanceProfileLevel]:
    """Validate an ordered, JSON-safe provider/profile fallback ladder."""
    if value in (None, "", []):
        return []
    if not isinstance(value, (list, tuple)):
        raise TypeError("maintenance profile ladder must be a JSON array")
    if not 1 <= len(value) <= 8:
        raise ValueError("maintenance profile ladder must contain 1 to 8 levels")
    levels: list[MaintenanceProfileLevel] = []
    names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TypeError(f"maintenance profile ladder level {index} must be an object")
        name = str(raw.get("name") or f"level-{index + 1}").strip()
        provider = str(raw.get("provider") or "").strip().lower()
        profile = str(raw.get("profile") or "balanced").strip().lower()
        model_value = raw.get("model")
        model = str(model_value).strip() if model_value not in (None, "") else None
        if not name or name in names:
            raise ValueError("maintenance profile ladder level names must be non-empty and unique")
        if not provider:
            raise ValueError(f"maintenance profile ladder level {name} needs a provider")
        if profile not in PROFILE_NAMES:
            raise ValueError(f"maintenance profile ladder level {name} has an invalid profile")
        names.add(name)
        levels.append(
            MaintenanceProfileLevel(
                name=name,
                provider=provider,
                model=model,
                profile=profile,
                budget=normalize_budget(raw.get("budget") if isinstance(raw.get("budget"), Mapping) else None),
            )
        )
    return levels


def configured_profile_ladder() -> list[MaintenanceProfileLevel]:
    """Load the optional ladder from one JSON environment variable."""
    raw = os.getenv("LLM_WIKI_MAINTENANCE_PROFILE_LADDER", "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM_WIKI_MAINTENANCE_PROFILE_LADDER must be valid JSON") from exc
    return normalize_profile_ladder(value)


def resolve_profile_ladder(
    *,
    enabled: bool,
    requested: str,
    budget: Mapping[str, object] | None,
    ladder: object,
    ladder_spend: Mapping[str, Mapping[str, Mapping[str, object]]] | None,
    estimate: Mapping[str, float],
) -> MaintenanceProfileLadderDecision:
    """Select primary first, then fallbacks; a reset naturally restores primary."""
    levels = normalize_profile_ladder(ladder)
    if not levels:
        profile = resolve_profile(enabled=enabled, requested=requested, budget=budget)
        return MaintenanceProfileLadderDecision(None, profile, None, profile.reason)
    if not enabled:
        profile = MaintenanceProfileDecision(requested, "off", False, "master_disabled")
        return MaintenanceProfileLadderDecision(None, profile, None, profile.reason)
    spend = ladder_spend or {}
    for index, level in enumerate(levels):
        level_budget = level.budget or normalize_budget(budget)
        fits, reason = budget_fits(level_budget, spend.get(level.name, {}), estimate)
        if fits:
            profile = MaintenanceProfileDecision(level.profile, level.profile, True, "ladder_selected")
            return MaintenanceProfileLadderDecision(level, profile, index, reason)
    profile = MaintenanceProfileDecision(requested, "off", False, "all_ladder_levels_exhausted")
    return MaintenanceProfileLadderDecision(None, profile, None, profile.reason)


def add_usage(
    spend: Mapping[str, Mapping[str, object]],
    usage: Mapping[str, float],
) -> dict[str, dict[str, float]]:
    """Return updated spend without mutating the caller's persisted mapping."""
    result = {window: {metric: float(value) for metric, value in metrics.items()} for window, metrics in spend.items()}
    for window in WINDOW_NAMES:
        metrics = result.setdefault(window, {})
        for metric, amount in usage.items():
            metrics[metric] = metrics.get(metric, 0.0) + max(0.0, float(amount))
    return {window: metrics for window, metrics in result.items() if metrics}


def reset_expired_spend(
    spend: Mapping[str, Mapping[str, object]],
    started_at: Mapping[str, object],
    *,
    now: float,
    mode: str = "rolling",
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Reset rolling (or calendar) windows without losing other window state."""
    if mode not in {"rolling", "calendar"}:
        raise ValueError("maintenance budget window mode must be rolling or calendar")
    refreshed = {window: {metric: float(value) for metric, value in metrics.items()} for window, metrics in spend.items()}
    starts = {window: float(value) for window, value in started_at.items() if window in WINDOW_NAMES}
    for window in WINDOW_NAMES:
        start = starts.get(window, float(now))
        if mode == "rolling":
            expired = float(now) - start >= WINDOW_SECONDS[window]
        else:
            import datetime as _datetime

            current = _datetime.datetime.fromtimestamp(float(now), _datetime.UTC)
            previous = _datetime.datetime.fromtimestamp(start, _datetime.UTC)
            expired = (current.date() != previous.date() if window == "daily" else (
                current.isocalendar()[:2] != previous.isocalendar()[:2] if window == "weekly"
                else (current.year, current.month) != (previous.year, previous.month)
            ))
        if expired:
            refreshed.pop(window, None)
            starts[window] = float(now)
        else:
            starts.setdefault(window, start)
    return refreshed, starts


def next_window_reset(now: float, window: str, *, mode: str = "rolling") -> float:
    """Return the next reset timestamp for a configured budget window."""
    if window not in WINDOW_SECONDS:
        raise ValueError(f"unknown maintenance budget window: {window}")
    if mode not in {"rolling", "calendar"}:
        raise ValueError("maintenance budget window mode must be rolling or calendar")
    if mode == "rolling":
        return float(now) + WINDOW_SECONDS[window]

    current = datetime.fromtimestamp(float(now), UTC)
    if window == "daily":
        boundary = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), UTC)
    elif window == "weekly":
        start_of_week = current.date() - timedelta(days=current.weekday())
        boundary = datetime.combine(start_of_week + timedelta(days=7), datetime.min.time(), UTC)
    else:
        if current.month == 12:
            boundary = datetime(current.year + 1, 1, 1, tzinfo=UTC)
        else:
            boundary = datetime(current.year, current.month + 1, 1, tzinfo=UTC)
    return boundary.timestamp()


__all__ = [
    "METRIC_NAMES",
    "MODEL_CLASSES",
    "PROFILE_NAMES",
    "WINDOW_NAMES",
    "MaintenanceProfileDecision",
    "MaintenanceProfileLadderDecision",
    "MaintenanceProfileLevel",
    "add_usage",
    "budget_fits",
    "configured_maintenance_budget",
    "configured_maintenance_enabled",
    "configured_maintenance_profile",
    "configured_prices",
    "configured_profile_ladder",
    "configured_token_budget_rate",
    "next_window_reset",
    "normalize_budget",
    "normalize_profile_ladder",
    "reset_expired_spend",
    "resolve_profile",
    "resolve_profile_ladder",
]
