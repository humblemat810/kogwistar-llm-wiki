"""Deterministic, auditable maintenance candidate selection."""
from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class MaintenanceCandidate:
    candidate_id: str
    reason: str
    score: float | None = None
    metadata: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _id(item: object) -> str:
    value = getattr(item, "safe_get_id", lambda: getattr(item, "id", ""))()
    return str(value or "")


def _meta(item: object) -> Mapping[str, object]:
    value = getattr(item, "metadata", None)
    return value if isinstance(value, Mapping) else {}


def _vector(item: object) -> list[float] | None:
    value = getattr(item, "embedding", None)
    if value is None:
        value = _meta(item).get("embedding")
    if not isinstance(value, (list, tuple)):
        return None
    try:
        vector = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    return vector if vector and all(math.isfinite(part) for part in vector) else None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        return None
    denominator = math.sqrt(sum(part * part for part in left)) * math.sqrt(sum(part * part for part in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else None


def _edge_endpoints(edge: object) -> set[str]:
    endpoints: set[str] = set()
    for field in ("source_ids", "target_ids"):
        value = getattr(edge, field, None)
        if isinstance(value, str):
            endpoints.add(value)
        elif isinstance(value, Iterable):
            endpoints.update(str(item) for item in value if item)
    return endpoints


def select_request_candidates(
    seed_nodes: Sequence[object],
    candidate_nodes: Sequence[object],
    edges: Sequence[object],
    *,
    max_candidates: int = 24,
    semantic_min_score: float = 0.2,
) -> list[MaintenanceCandidate]:
    """Select connected, semantic, then evidence/history-related candidates."""
    seeds = {_id(node) for node in seed_nodes if _id(node)}
    connected: set[str] = set()
    for edge in edges:
        endpoints = _edge_endpoints(edge)
        if endpoints & seeds:
            connected.update(endpoints - seeds)
    by_id = {_id(node): node for node in candidate_nodes if _id(node)}
    output: list[MaintenanceCandidate] = [
        MaintenanceCandidate(node_id, "connected_neighbor", metadata={})
        for node_id in sorted(connected)
        if node_id in by_id
    ]
    selected = {item.candidate_id for item in output} | seeds
    seed_vectors = [_vector(node) for node in seed_nodes]
    seed_vectors = [vector for vector in seed_vectors if vector is not None]
    semantic: list[MaintenanceCandidate] = []
    if seed_vectors:
        for node_id, node in by_id.items():
            if node_id in selected:
                continue
            scores = [_cosine(vector, seed) for vector in seed_vectors for seed in [_vector(node)] if seed is not None]
            score = max((value for value in scores if value is not None), default=None)
            if score is not None and score >= float(semantic_min_score):
                semantic.append(MaintenanceCandidate(node_id, "semantic_similar", score=score, metadata={}))
    output.extend(sorted(semantic, key=lambda item: (-float(item.score or 0), item.candidate_id)))
    selected = {item.candidate_id for item in output} | seeds
    for node_id, node in sorted(by_id.items()):
        if node_id in selected:
            continue
        metadata = _meta(node)
        if any(key in metadata for key in ("source_ids", "evidence_ids", "history_ids", "related_node_ids")):
            reason = "history_related" if "history_ids" in metadata else "shared_evidence"
            output.append(MaintenanceCandidate(node_id, reason, metadata={}))
    return output[: max(0, int(max_candidates))]


def select_embedding_exploration(
    nodes: Sequence[object],
    *,
    dimension: int,
    cycle_seed: int,
    max_candidates: int = 12,
    excluded_ids: set[str] | None = None,
) -> tuple[list[MaintenanceCandidate], str]:
    """Probe a deterministic random point in embedding space, not row IDs."""
    rng = random.Random(int(cycle_seed))
    probe = [rng.gauss(0.0, 1.0) for _ in range(max(1, int(dimension)))]
    excluded = excluded_ids or set()
    ranked: list[MaintenanceCandidate] = []
    for node in nodes:
        node_id = _id(node)
        vector = _vector(node)
        if not node_id or node_id in excluded or vector is None or len(vector) != len(probe):
            continue
        score = _cosine(probe, vector)
        if score is not None:
            ranked.append(MaintenanceCandidate(node_id, "semantic_similar", score=score, metadata={"probe_seed": int(cycle_seed)}))
    ranked.sort(key=lambda item: (-float(item.score or 0), item.candidate_id))
    return ranked[: max(0, int(max_candidates))], "embedding_probe" if ranked else "embedding_unavailable"


__all__ = ["MaintenanceCandidate", "select_embedding_exploration", "select_request_candidates"]
