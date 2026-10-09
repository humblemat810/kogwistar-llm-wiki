"""Ephemeral graph-ID aliases for model-facing prompts.

Aliases in this module are a presentation projection only.  The caller keeps
the canonical snapshot and restores canonical IDs before validation, logging,
or persistence.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from kogwistar.engine_core.utils import AliasBook

from .semantic_lens import SemanticLensSnapshot

_NODE_KEYS = frozenset({
    "node_id", "node_ids", "from_node_id", "to_node_id", "entity_id",
    "entity_ids", "cited_entity_ids", "invalid_anchor_ids", "anchor_id",
    "source_ids", "target_ids", "active_node_ids", "visible_node_ids",
})
_EDGE_KEYS = frozenset({
    "edge_id", "edge_ids", "supersedes_edge_id", "supersedes_ids",
    "active_edge_ids", "visible_edge_ids",
})


class PromptAliasError(ValueError):
    """Raised when a provider returns an invalid prompt alias."""


class CockpitActionLike(Protocol):
    """Typed surface required to restore a model-facing cockpit action."""

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class CockpitActionFactory(Protocol):
    """Pydantic-like class surface used after alias restoration."""

    @classmethod
    def model_validate(cls, value: Mapping[str, object]) -> CockpitActionLike: ...


class ModelDumpLike(Protocol):
    def model_dump(self, *, mode: str) -> dict[str, object]: ...


def _items(value: object) -> Sequence[object]:
    if isinstance(value, (list, tuple)):
        return value
    return ()


def _string_items(value: object) -> list[str]:
    return [str(item) for item in _items(value) if item]


def _dict_items(value: object) -> list[dict[str, object]]:
    return [item for item in _items(value) if isinstance(item, dict)]


def _object_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _project_id(book: AliasBook, value: object, *, kind: str) -> object:
    if not isinstance(value, str) or not value:
        return value
    if kind == "node":
        return book.alias_for_node(value)
    return book.alias_for_edge(value)


def _project_value(book: AliasBook, key: str | None, value: object) -> object:
    if key in _NODE_KEYS:
        if isinstance(value, list):
            return [_project_id(book, item, kind="node") for item in value]
        return _project_id(book, value, kind="node")
    if key in _EDGE_KEYS:
        if isinstance(value, list):
            return [_project_id(book, item, kind="edge") for item in value]
        return _project_id(book, value, kind="edge")
    if isinstance(value, Mapping):
        return {str(k): _project_value(book, str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_project_value(book, None, item) for item in value]
    if isinstance(value, tuple):
        return [_project_value(book, None, item) for item in value]
    return value


def _collect_ids(value: object, *, nodes: set[str], edges: set[str], key: str | None = None) -> None:
    if key in _NODE_KEYS:
        target = nodes
    elif key in _EDGE_KEYS:
        target = edges
    else:
        target = None
    if target is not None:
        if isinstance(value, str) and value:
            target.add(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            target.update(str(item) for item in value if item)
        return
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            _collect_ids(child_value, nodes=nodes, edges=edges, key=str(child_key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _collect_ids(item, nodes=nodes, edges=edges)


def _collect_snapshot_ids(canonical: Mapping[str, object]) -> tuple[set[str], set[str]]:
    nodes: set[str] = set()
    edges: set[str] = set()
    for item in _items(canonical.get("nodes")):
        if isinstance(item, Mapping) and item.get("id"):
            nodes.add(str(item["id"]))
    for collection_name in ("edges", "hyperedges"):
        for item in _items(canonical.get(collection_name)):
            if not isinstance(item, Mapping):
                continue
            if item.get("id"):
                edges.add(str(item["id"]))
            nodes.update(_string_items(item.get("source_ids")))
            nodes.update(_string_items(item.get("target_ids")))
            edges.update(_string_items(item.get("source_edge_ids")))
            edges.update(_string_items(item.get("target_edge_ids")))
    for item in _items(canonical.get("participations")):
        if isinstance(item, Mapping):
            if item.get("node_id"):
                nodes.add(str(item["node_id"]))
            if item.get("edge_id"):
                edges.add(str(item["edge_id"]))
    for collection_name in ("anchor_explanations", "selection_explanations"):
        for item in _items(canonical.get(collection_name)):
            if isinstance(item, Mapping) and item.get("node_id"):
                nodes.add(str(item["node_id"]))
    for item in _items(canonical.get("flat_hits")):
        if isinstance(item, Mapping):
            if item.get("node_id"):
                nodes.add(str(item["node_id"]))
            if item.get("edge_id"):
                edges.add(str(item["edge_id"]))
    _collect_ids(canonical.get("observations"), nodes=nodes, edges=edges)
    return nodes, edges


def _project_snapshot(book: AliasBook, canonical: Mapping[str, object]) -> dict[str, object]:
    projected = copy.deepcopy(dict(canonical))
    for item in _dict_items(projected.get("nodes")):
        if isinstance(item, dict) and item.get("id"):
            item["id"] = book.alias_for_node(str(item["id"]))
    for collection_name in ("edges", "hyperedges"):
        for item in _dict_items(projected.get(collection_name)):
            if item.get("id"):
                item["id"] = book.alias_for_edge(str(item["id"]))
            item["source_ids"] = [book.alias_for_node(str(value)) for value in _items(item.get("source_ids"))]
            item["target_ids"] = [book.alias_for_node(str(value)) for value in _items(item.get("target_ids"))]
            for field in ("source_edge_ids", "target_edge_ids"):
                if field in item:
                    item[field] = [book.alias_for_edge(str(value)) for value in _items(item.get(field))]
    for item in _dict_items(projected.get("participations")):
        if item.get("node_id"):
            item["node_id"] = book.alias_for_node(str(item["node_id"]))
        if item.get("edge_id"):
            item["edge_id"] = book.alias_for_edge(str(item["edge_id"]))
    for collection_name in ("anchor_explanations", "selection_explanations"):
        for item in _dict_items(projected.get(collection_name)):
            if item.get("node_id"):
                item["node_id"] = book.alias_for_node(str(item["node_id"]))
    for item in _dict_items(projected.get("flat_hits")):
        if item.get("node_id"):
            item["node_id"] = book.alias_for_node(str(item["node_id"]))
        if item.get("edge_id"):
            item["edge_id"] = book.alias_for_edge(str(item["edge_id"]))
    if isinstance(projected.get("observations"), list):
        projected["observations"] = [
            _project_value(book, None, observation)
            for observation in _items(projected["observations"])
        ]
    return projected


def _collect_crosslink_ids(canonical: Mapping[str, object]) -> tuple[set[str], set[str]]:
    nodes: set[str] = set()
    edges: set[str] = set()
    for item in _items(canonical.get("evidence")):
        if isinstance(item, Mapping) and item.get("node_id"):
            nodes.add(str(item["node_id"]))
    context = canonical.get("neighbor_context")
    if isinstance(context, Mapping):
        for item in _items(context.get("nodes")):
            if isinstance(item, Mapping) and (item.get("node_id") or item.get("id")):
                nodes.add(str(item.get("node_id") or item.get("id")))
        for item in _items(context.get("edges")):
            if not isinstance(item, Mapping):
                continue
            if item.get("edge_id") or item.get("id"):
                edges.add(str(item.get("edge_id") or item.get("id")))
            nodes.update(_string_items(item.get("source_ids")))
            nodes.update(_string_items(item.get("target_ids")))
    return nodes, edges


def _project_crosslink(book: AliasBook, canonical: Mapping[str, object]) -> dict[str, object]:
    projected = copy.deepcopy(dict(canonical))
    for item in _dict_items(projected.get("evidence")):
        if item.get("node_id"):
            item["node_id"] = book.alias_for_node(str(item["node_id"]))
    context = projected.get("neighbor_context")
    if isinstance(context, dict):
        for item in _dict_items(context.get("nodes")):
            if item.get("node_id"):
                item["node_id"] = book.alias_for_node(str(item["node_id"]))
        for item in _dict_items(context.get("edges")):
            if item.get("edge_id"):
                item["edge_id"] = book.alias_for_edge(str(item["edge_id"]))
            elif item.get("id"):
                item["id"] = book.alias_for_edge(str(item["id"]))
            item["source_ids"] = [book.alias_for_node(str(value)) for value in _items(item.get("source_ids"))]
            item["target_ids"] = [book.alias_for_node(str(value)) for value in _items(item.get("target_ids"))]
            for field in ("source_edge_ids", "target_edge_ids"):
                if field in item:
                    item[field] = [book.alias_for_edge(str(value)) for value in _items(item.get(field))]
    return projected


@dataclass(frozen=True, slots=True)
class PromptAliasProjection:
    """One immutable prompt projection and its request-local resolver."""

    context: dict[str, object]
    canonical_digest: str
    alias_schema_version: int = 1
    _book: AliasBook | None = None

    @classmethod
    def for_snapshot(
        cls,
        snapshot: SemanticLensSnapshot,
        observations: Sequence[object] = (),
    ) -> PromptAliasProjection:
        canonical = snapshot.to_dict()
        canonical_observations = _items(canonical.get("observations"))
        for observation in observations:
            if hasattr(observation, "model_dump"):
                canonical_observation = cast(ModelDumpLike, observation).model_dump(mode="json")
            else:
                canonical_observation = observation
            canonical_observations = [*canonical_observations, canonical_observation]
        if canonical_observations:
            canonical["observations"] = canonical_observations
        nodes, edges = _collect_snapshot_ids(canonical)
        book = AliasBook.deterministic(sorted(nodes), sorted(edges))
        projected = _project_snapshot(book, canonical)
        if not isinstance(projected, dict):
            raise PromptAliasError("snapshot projection did not produce an object")
        return cls(
            context=projected,
            canonical_digest=_digest(canonical),
            _book=book,
        )

    def resolve_node(self, value: str) -> str:
        if self._book is None:
            raise PromptAliasError("projection has no resolver")
        return self._book.resolve_node(value)

    def resolve_edge(self, value: str) -> str:
        if self._book is None:
            raise PromptAliasError("projection has no resolver")
        return self._book.resolve_edge(value)

    def resolve_any(self, value: str) -> str:
        if self._book is None:
            raise PromptAliasError("projection has no resolver")
        if value.startswith("N"):
            return self._book.resolve_node(value)
        if value.startswith("E"):
            return self._book.resolve_edge(value)
        return value

    def project_mapping(self, value: Mapping[str, object]) -> dict[str, object]:
        projected = _project_value(self._book_or_raise(), None, value)
        if not isinstance(projected, dict):
            raise PromptAliasError("mapping projection did not produce an object")
        return projected

    def _book_or_raise(self) -> AliasBook:
        if self._book is None:
            raise PromptAliasError("projection has no resolver")
        return self._book

    def metadata(self) -> dict[str, object]:
        return {
            "alias_schema_version": self.alias_schema_version,
            "canonical_context_digest": self.canonical_digest,
        }


def restore_cockpit_action(
    action: CockpitActionLike,
    projection: PromptAliasProjection,
) -> CockpitActionLike:
    """Restore graph references in a typed cockpit action before host validation."""
    restored = action.model_dump(mode="python")
    restored["entity_ids"] = [projection.resolve_any(value) for value in _string_items(restored.get("entity_ids"))]
    restored["cited_entity_ids"] = [
        projection.resolve_any(value) for value in _string_items(restored.get("cited_entity_ids"))
    ]
    patch = restored.get("patch")
    if isinstance(patch, Mapping):
        patch = copy.deepcopy(dict(patch))
        for operation in _dict_items(patch.get("operations")):
            raw_kind = operation.get("kind")
            kind = str(getattr(raw_kind, "value", raw_kind) or "")
            if operation.get("from_node_id"):
                operation["from_node_id"] = projection.resolve_node(str(operation["from_node_id"]))
            if operation.get("to_node_id"):
                operation["to_node_id"] = projection.resolve_node(str(operation["to_node_id"]))
            if operation.get("supersedes_ids"):
                operation["supersedes_ids"] = [
                    projection.resolve_edge(value) for value in _string_items(operation["supersedes_ids"])
                ]
            if kind == "ADD_NODE":
                node_id = operation.get("node_id")
                if node_id and not str(node_id).startswith("nn:"):
                    operation["node_id"] = projection.resolve_node(str(operation["node_id"]))
            elif kind == "ADD_EDGE":
                edge_id = operation.get("edge_id")
                if edge_id and not str(edge_id).startswith("ne:"):
                    operation["edge_id"] = projection.resolve_edge(str(operation["edge_id"]))
            elif kind == "TOMBSTONE_NODE":
                for field in ("target_id", "node_id"):
                    if operation.get(field):
                        operation[field] = projection.resolve_node(str(operation[field]))
            elif kind == "TOMBSTONE_EDGE":
                for field in ("target_id", "edge_id"):
                    if operation.get(field):
                        operation[field] = projection.resolve_edge(str(operation[field]))
        restored["patch"] = patch
    factory = cast(CockpitActionFactory, type(action))
    return factory.model_validate(restored)


def project_crosslink_payload(
    evidence: Sequence[Mapping[str, object]],
    neighbor_context: Mapping[str, object],
) -> tuple[PromptAliasProjection, list[dict[str, object]], dict[str, object]]:
    """Alias graph IDs in a cross-link provider request while keeping evidence IDs stable."""
    canonical = {
        "evidence": [dict(item) for item in evidence],
        "neighbor_context": dict(neighbor_context),
    }
    nodes, edges = _collect_crosslink_ids(canonical)
    book = AliasBook.deterministic(sorted(nodes), sorted(edges))
    projected = _project_crosslink(book, canonical)
    if not isinstance(projected, dict):
        raise PromptAliasError("cross-link projection did not produce an object")
    projection = PromptAliasProjection(
        context=projected,
        canonical_digest=_digest(canonical),
        _book=book,
    )
    return (
        projection,
        _dict_items(projected.get("evidence")),
        _object_mapping(projected.get("neighbor_context")),
    )


def restore_crosslink_response(
    response: Mapping[str, object], projection: PromptAliasProjection
) -> dict[str, object]:
    """Restore only graph references in a provider response."""
    restored = copy.deepcopy(dict(response))
    for group in _dict_items(restored.get("groups")):
        for operation in _dict_items(group.get("operations")):
            if operation.get("supersedes_edge_id"):
                operation["supersedes_edge_id"] = projection.resolve_edge(
                    str(operation["supersedes_edge_id"])
                )
    return restored


__all__ = [
    "CockpitActionLike",
    "PromptAliasError",
    "PromptAliasProjection",
    "project_crosslink_payload",
    "restore_cockpit_action",
    "restore_crosslink_response",
]
