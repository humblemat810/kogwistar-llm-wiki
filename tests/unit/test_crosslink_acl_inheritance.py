from __future__ import annotations

from types import SimpleNamespace

from kogwistar_llm_wiki.maintenance.maintenance_patch_apply import (
    _crosslink_acl_metadata,
)
from kogwistar_llm_wiki.maintenance.maintenance_patches import (
    MaintenanceOperationKind,
    MaintenancePatchOperation,
)


def _operation() -> MaintenancePatchOperation:
    return MaintenancePatchOperation(
        operation_id="crosslink-acl",
        kind=MaintenanceOperationKind.ADD_EDGE,
        edge_id="edge-derived",
        from_node_id="node-a",
        to_node_id="node-b",
        relation="supports",
    )


def _engine(left: object, right: object) -> SimpleNamespace:
    records = {"node-a": left, "node-b": right}
    acl_graph = SimpleNamespace(
        latest_record=lambda *, grain, truth_graph, entity_id: records.get(entity_id)
    )
    return SimpleNamespace(acl_enabled=True, kg_graph_type="knowledge", acl_graph=acl_graph)


def _record(
    *,
    mode: str = "public",
    owner_id: str = "owner",
    security_scope: str = "scope-a",
    principals: tuple[str, ...] = (),
    groups: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        mode=mode,
        owner_id=owner_id,
        security_scope=security_scope,
        shared_with_principals=principals,
        shared_with_groups=groups,
    )


def test_crosslink_acl_inherits_matching_endpoint_policy() -> None:
    metadata, issues = _crosslink_acl_metadata(
        _engine(_record(principals=("reader-a",)), _record(principals=("reader-a",))),
        _operation(),
    )

    assert issues == []
    assert metadata == {
        "acl_mode": "public",
        "owner_id": "owner",
        "security_scope": "scope-a",
        "shared_with_agents": ["reader-a"],
        "shared_with_groups": [],
    }


def test_crosslink_acl_rejects_incompatible_endpoint_policies() -> None:
    metadata, issues = _crosslink_acl_metadata(
        _engine(_record(), _record(mode="private")),
        _operation(),
    )

    assert metadata == {}
    assert [issue.code for issue in issues] == ["acl_policy_incompatible"]


def test_crosslink_acl_fails_closed_when_endpoint_policy_is_missing() -> None:
    metadata, issues = _crosslink_acl_metadata(_engine(_record(), None), _operation())

    assert metadata == {}
    assert [issue.code for issue in issues] == ["acl_policy_unavailable"]
