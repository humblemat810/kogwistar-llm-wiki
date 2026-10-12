from __future__ import annotations

import pytest

from kogwistar_llm_wiki.app_contracts import (
    ObsidianSourceHostAdapter,
    validate_source_request,
)


def test_obsidian_source_host_adapter_keeps_callbacks_typed_and_explicit() -> None:
    calls: list[tuple[str, object]] = []

    host = ObsidianSourceHostAdapter(
        export_snapshot=lambda workspace, vault: {"workspace": workspace, "vault": vault},
        scan_import=lambda workspace, vault, payload: {"operation": "scan", "payload": payload},
        plan_import=lambda workspace, vault, payload: {"operation": "plan", "payload": payload},
        apply_import=lambda workspace, vault, plan, mode: {"plan": plan, "mode": mode},
        list_conflicts=lambda workspace, vault: {"workspace": workspace, "vault": vault},
    )

    assert host.export_snapshot("w", "v") == {"workspace": "w", "vault": "v"}
    assert host.scan_import("w", "v", {"x": 1}) == {"operation": "scan", "payload": {"x": 1}}
    assert host.plan_import("w", "v", {"x": 1}) == {"operation": "plan", "payload": {"x": 1}}
    assert host.apply_import("w", "v", "plan", "ignore") == {"plan": "plan", "mode": "ignore"}
    assert host.list_conflicts("w", "v") == {"workspace": "w", "vault": "v"}
    assert calls == []


def test_obsidian_source_request_requires_bounded_workspace_and_vault_identity() -> None:
    assert validate_source_request({"workspace_id": "w", "vault_id": "v"}) == ("w", "v")
    with pytest.raises(ValueError, match="must be strings"):
        validate_source_request({"workspace_id": 1, "vault_id": "v"})
