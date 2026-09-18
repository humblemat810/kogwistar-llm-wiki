"""Bounded application-level dependency invalidation.

Kogwistar already provides namespace-scoped active node and edge reads.  This
module deliberately builds on those reads instead of adding a parser-specific
reverse-reference primitive to the substrate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

_FOLLOW_UP_KINDS_BY_ARTIFACT: dict[str, tuple[str, ...]] = {
    "candidate_link": ("document_propose_crosslinks",),
    "crosslink": ("document_propose_crosslinks",),
    "derived_summary": ("document_summarize_units",),
    "summary": ("document_summarize_units",),
    "promotion_candidate": ("conversation_promote_to_kg",),
    "promotion_evidence_pack": ("conversation_promote_to_kg",),
    "projection_status_event": (),
}


@dataclass(frozen=True, slots=True)
class DependencyInvalidationPlan:
    """A deterministic, non-mutating invalidation decision."""

    workspace_id: str
    changed_entity_ids: tuple[str, ...]
    affected_entity_ids: tuple[str, ...]
    affected_source_document_ids: tuple[str, ...]
    follow_up_kinds: tuple[str, ...]
    skipped_cross_workspace_ids: tuple[str, ...] = ()
    truncated: bool = False


def plan_dependency_invalidation(
    *,
    workspace_id: str,
    changed_entity_ids: Iterable[str],
    nodes: Sequence[object],
    edges: Sequence[object],
    max_dependents: int = 256,
) -> DependencyInvalidationPlan:
    """Find same-workspace derived artifacts affected by changed IDs.

    The function is intentionally read-only.  It follows explicit metadata
    lineage and active edge endpoints only; a missing workspace declaration is
    not treated as local, preventing legacy or foreign artifacts from crossing
    workspace boundaries accidentally.
    """

    if max_dependents < 1:
        raise ValueError("max_dependents must be positive")
    changed = {str(value).strip() for value in changed_entity_ids if str(value).strip()}
    affected: set[str] = set()
    source_ids: set[str] = set()
    follow_up_kinds: set[str] = set()
    skipped: set[str] = set()

    def metadata(item: object) -> dict[str, object]:
        raw = getattr(item, "metadata", {})
        return dict(raw) if isinstance(raw, Mapping) else {}

    def ids_from(value: object) -> set[str]:
        if isinstance(value, str):
            return {value} if value.strip() else set()
        if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
            return {str(item).strip() for item in value if str(item).strip()}
        return set()

    def consider(item: object, references: set[str]) -> None:
        item_id = str(getattr(item, "id", "") or "").strip()
        if not item_id or not references & changed:
            return
        item_metadata = metadata(item)
        if str(item_metadata.get("workspace_id") or "") != workspace_id:
            if item_id:
                skipped.add(item_id)
            return
        artifact_kind = str(item_metadata.get("artifact_kind") or "").strip()
        if artifact_kind not in _FOLLOW_UP_KINDS_BY_ARTIFACT:
            return
        affected.add(item_id)
        source_ids.update(ids_from(item_metadata.get("source_document_ids")))
        source_id = str(item_metadata.get("source_document_id") or "").strip()
        if source_id:
            source_ids.add(source_id)
        follow_up_kinds.update(_FOLLOW_UP_KINDS_BY_ARTIFACT[artifact_kind])

    for node in nodes:
        item_metadata = metadata(node)
        references = set()
        for key in (
            "depends_on_ids",
            "derived_from_ids",
            "lineage_node_ids",
            "lineage_edge_ids",
            "source_document_ids",
            "source_document_id",
            "parse_generation_member_id",
        ):
            references.update(ids_from(item_metadata.get(key)))
        consider(node, references)

    for edge in edges:
        references = ids_from(getattr(edge, "source_ids", ())) | ids_from(
            getattr(edge, "target_ids", ())
        )
        edge_metadata = metadata(edge)
        for key in ("depends_on_ids", "derived_from_ids", "lineage_node_ids", "lineage_edge_ids"):
            references.update(ids_from(edge_metadata.get(key)))
        consider(edge, references)

    ordered_affected = tuple(sorted(affected))
    truncated = len(ordered_affected) > max_dependents
    if truncated:
        ordered_affected = ordered_affected[:max_dependents]
    return DependencyInvalidationPlan(
        workspace_id=workspace_id,
        changed_entity_ids=tuple(sorted(changed)),
        affected_entity_ids=ordered_affected,
        affected_source_document_ids=tuple(sorted(source_ids)),
        follow_up_kinds=tuple(sorted(follow_up_kinds)),
        skipped_cross_workspace_ids=tuple(sorted(skipped)),
        truncated=truncated,
    )


__all__ = ["DependencyInvalidationPlan", "plan_dependency_invalidation"]
