"""Build a reversible, ACL-scoped address-book projection from reviewed identity links."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice

from .contact_matching import (
    ContactIdentityObservation,
    ContactPointClaim,
    contact_evidence_snapshot_id,
)
from .disambiguation_contracts import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
)

AuthorizeContactStream = Callable[[str, str], bool]
ContactObservationProvider = Callable[
    [str, int, AuthorizeContactStream], Sequence[ContactIdentityObservation]
]
ContactScanObservationProvider = Callable[
    [str, Mapping[str, object], AuthorizeContactStream],
    Sequence[ContactIdentityObservation],
]


def compose_contact_scan_observation_providers(
    providers: Mapping[str, ContactScanObservationProvider],
    *,
    max_observations: int = 250,
) -> Callable[
    [str, Mapping[str, object], AuthorizeContactStream],
    tuple[ContactIdentityObservation, ...],
]:
    """Compose trusted channel readers for one ACL-first maintenance scan.

    Each reader must authorize its own streams before reading evidence. The
    composition validates trigger scope first, then validates every returned
    observation and refuses partial, cross-workspace, or colliding results.
    """

    if not isinstance(providers, Mapping) or not providers:
        raise ValueError("at least one contact scan provider is required")
    if type(max_observations) is not int or max_observations < 1:
        raise ValueError("max_observations must be positive")
    configured: list[tuple[str, ContactScanObservationProvider]] = []
    for name, provider in providers.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("contact scan provider names must be non-empty strings")
        if not callable(provider):
            raise TypeError("contact scan providers must be callable")
        configured.append((name.strip(), provider))
    configured.sort(key=lambda item: item[0])
    if len({name for name, _provider in configured}) != len(configured):
        raise ValueError("contact scan provider names must be unique")

    def provide(
        workspace_id: str,
        payload: Mapping[str, object],
        authorize_stream: AuthorizeContactStream,
    ) -> tuple[ContactIdentityObservation, ...]:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if not isinstance(payload, Mapping) or payload.get("workspace_id") != workspace_id:
            raise ValueError("contact scan payload must match the requested workspace")
        if not callable(authorize_stream):
            raise TypeError("contact scan stream authorizer is required")
        raw_stream_ids = payload.get("source_stream_ids")
        if not isinstance(raw_stream_ids, (list, tuple)) or not raw_stream_ids:
            raise ValueError("contact scan requires changed source_stream_ids")
        if len(raw_stream_ids) > max_observations:
            raise ValueError("contact scan has too many changed source streams")
        stream_ids: list[str] = []
        for stream_id in raw_stream_ids:
            if not isinstance(stream_id, str) or not stream_id.strip():
                raise ValueError("source_stream_ids must contain non-empty strings")
            stream_ids.append(stream_id.strip())
        if len(set(stream_ids)) != len(stream_ids):
            raise ValueError("source_stream_ids must not contain duplicates")
        for stream_id in sorted(stream_ids):
            if not authorize_stream(workspace_id, stream_id):
                raise PermissionError("changed contact source stream is not authorized")

        results: list[ContactIdentityObservation] = []
        seen_entity_ids: set[str] = set()
        provider_payload = dict(payload)
        provider_payload["source_stream_ids"] = tuple(stream_ids)
        for name, provider in configured:
            batch = provider(workspace_id, provider_payload.copy(), authorize_stream)
            if not isinstance(batch, Sequence):
                raise TypeError(f"contact scan provider {name!r} must return a bounded sequence")
            if len(batch) > max_observations or len(results) + len(batch) > max_observations:
                raise ValueError("composed contact scan exceeds max_observations")
            for observation in batch:
                if not isinstance(observation, ContactIdentityObservation):
                    raise TypeError(f"contact scan provider {name!r} returned an invalid observation")
                if observation.workspace_id != workspace_id:
                    raise ValueError("contact scan provider returned another workspace")
                if observation.entity_id in seen_entity_ids:
                    raise ValueError("contact scan entity IDs collide across providers")
                if not authorize_stream(workspace_id, observation.stream_id):
                    raise PermissionError("contact scan provider returned an unauthorized stream")
                seen_entity_ids.add(observation.entity_id)
                results.append(observation)
        return tuple(sorted(results, key=lambda item: (item.entity_id, item.stream_id)))

    return provide


def compose_contact_observation_providers(
    providers: Mapping[str, ContactObservationProvider],
    *,
    max_observations: int = 5000,
) -> ContactObservationProvider:
    """Compose optional channel adapters behind one bounded, ACL-aware seam.

    Providers must authorize each stream before reading its evidence. This
    adapter passes the authorizer through, validates returned scope, rejects
    colliding entity IDs rather than guessing identity, and never truncates a
    partial channel set into a plausible-but-incomplete address book.
    """

    if not isinstance(providers, Mapping) or not providers:
        raise ValueError("at least one contact observation provider is required")
    if type(max_observations) is not int or max_observations < 1:
        raise ValueError("max_observations must be positive")
    configured: list[tuple[str, ContactObservationProvider]] = []
    for channel, provider in providers.items():
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("contact provider names must be non-empty strings")
        if not callable(provider):
            raise TypeError("contact providers must be callable")
        configured.append((channel.strip(), provider))
    names = [name for name, _provider in configured]
    if len(set(names)) != len(names):
        raise ValueError("contact provider names must be unique after normalization")
    configured.sort(key=lambda item: item[0])

    def provide(
        workspace_id: str,
        limit: int,
        authorize_stream: AuthorizeContactStream,
    ) -> tuple[ContactIdentityObservation, ...]:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if type(limit) is not int or not 1 <= limit <= max_observations:
            raise ValueError("limit must be within the configured observation bound")
        if not callable(authorize_stream):
            raise TypeError("authorize_stream must be callable")

        observations: list[ContactIdentityObservation] = []
        seen_entities: set[str] = set()
        for channel, provider in configured:
            batch = provider(workspace_id, limit, authorize_stream)
            if not isinstance(batch, Sequence):
                raise TypeError(f"contact provider {channel!r} must return a bounded sequence")
            if len(batch) > limit or len(observations) + len(batch) > limit:
                raise ValueError("composed contact observation batch exceeds limit")
            for observation in batch:
                if not isinstance(observation, ContactIdentityObservation):
                    raise TypeError(f"contact provider {channel!r} returned an invalid observation")
                if observation.workspace_id != workspace_id:
                    raise ValueError("contact provider returned an observation from another workspace")
                if observation.entity_id in seen_entities:
                    raise ValueError("contact provider entity IDs collide; source adapters must namespace IDs")
                if not authorize_stream(workspace_id, observation.stream_id):
                    raise PermissionError("contact provider returned an unauthorized stream")
                seen_entities.add(observation.entity_id)
                observations.append(observation)
        return tuple(sorted(observations, key=lambda item: (item.entity_id, item.stream_id)))

    return provide


@dataclass(frozen=True, slots=True)
class AddressBookClaim:
    """One contact-point claim with its original source provenance intact."""

    point: ContactPointClaim
    stream_id: str
    entity_id: str
    source_document_ids: tuple[str, ...]
    evidence_revision_ids: tuple[str, ...]
    observed_at_ms: int
    source_document_count: int | None = None
    evidence_revision_count: int | None = None
    display_name_count: int | None = None


@dataclass(frozen=True, slots=True)
class AddressBookEntry:
    """Read projection only; identity changes remain disambiguation decisions."""

    workspace_id: str
    contact_id: str
    entity_ids: tuple[str, ...]
    display_names: tuple[str, ...]
    contact_points: tuple[AddressBookClaim, ...]


def build_address_book_projection(
    observations: Iterable[ContactIdentityObservation],
    decisions: Iterable[DisambiguationCandidate],
    *,
    authorize_stream: AuthorizeContactStream,
    max_observations: int = 5000,
    max_decisions: int = 5000,
) -> tuple[AddressBookEntry, ...]:
    """Group contacts only through current, resolved SAME_ENTITY decisions.

    Similarity candidates never merge entries. A reviewed DISTINCT_ENTITIES
    relation blocks a transitive merge; contradictory decision graphs fail
    closed. No input evidence, graph entity, or ACL is mutated.
    """

    if not callable(authorize_stream):
        raise TypeError("authorize_stream must be callable")
    for name, bound in (("max_observations", max_observations), ("max_decisions", max_decisions)):
        if type(bound) is not int or bound < 1:
            raise ValueError(f"{name} must be positive")

    items = tuple(islice(observations, max_observations + 1))
    if len(items) > max_observations:
        raise ValueError("address-book projection input exceeds configured bounds")
    reviewed = tuple(islice(decisions, max_decisions + 1))
    if len(reviewed) > max_decisions:
        raise ValueError("address-book projection input exceeds configured bounds")
    if not items:
        if reviewed:
            raise ValueError("address-book decisions require source observations")
        return ()

    workspace_ids = {item.workspace_id for item in items}
    if len(workspace_ids) != 1:
        raise ValueError("address-book projection cannot mix workspaces")
    workspace_id = next(iter(workspace_ids))
    observations_by_id = {item.entity_id: item for item in items}
    if len(observations_by_id) != len(items):
        raise ValueError("address-book observations must have unique entity IDs")

    required_streams = {item.stream_id for item in items}
    prepared_decisions: dict[str, DisambiguationCandidate] = {}
    for candidate in reviewed:
        if candidate.workspace_id != workspace_id or not candidate.candidate_key.startswith("contact-match:"):
            raise ValueError("address-book decision is outside contact workspace scope")
        if any(entity_id not in observations_by_id for entity_id in candidate.entity_ids):
            continue
        prior = prepared_decisions.get(candidate.candidate_key)
        if prior is not None:
            raise ValueError("address-book input must contain one current decision per candidate")
        prepared_decisions[candidate.candidate_key] = candidate
        raw_streams = candidate.metadata.get("source_stream_ids")
        if not isinstance(raw_streams, str):
            raise TypeError("address-book decision lacks source stream provenance")
        try:
            streams = json.loads(raw_streams)
        except ValueError as exc:
            raise ValueError("address-book decision has invalid stream provenance") from exc
        if not isinstance(streams, list) or not streams or any(
            not isinstance(stream, str) or not stream.strip() for stream in streams
        ):
            raise ValueError("address-book decision has invalid stream provenance")
        expected_streams = {
            observations_by_id[entity_id].stream_id
            for entity_id in candidate.entity_ids
            if entity_id in observations_by_id
        }
        if len(candidate.entity_ids) != 2 or set(streams) != expected_streams:
            raise ValueError("address-book decision provenance does not match its entities")
        required_streams.update(streams)

    # Authorize the complete projection scope before exposing even one entry.
    if any(not authorize_stream(workspace_id, stream) for stream in sorted(required_streams)):
        raise PermissionError("address-book source stream is not authorized")

    parent = {entity_id: entity_id for entity_id in observations_by_id}

    def find(entity_id: str) -> str:
        while parent[entity_id] != entity_id:
            parent[entity_id] = parent[parent[entity_id]]
            entity_id = parent[entity_id]
        return entity_id

    same_pairs: list[tuple[str, str, str | None]] = []
    distinct_pairs: set[tuple[str, str]] = set()
    for candidate in reviewed:
        if candidate.artifact_status != DisambiguationArtifactStatus.RESOLVED:
            continue
        pair = tuple(sorted(candidate.entity_ids))
        if len(pair) != 2:
            continue
        basis = candidate.metadata.get("match_basis")
        if not isinstance(basis, str) or contact_evidence_snapshot_id(
            observations_by_id[pair[0]],
            observations_by_id[pair[1]],
            basis=basis,
        ) != candidate.evidence_snapshot_id:
            continue
        if candidate.semantic_decision == DisambiguationDecisionKind.SAME_ENTITY:
            if candidate.resolution_source.value not in {"user", "prior_decision"}:
                raise ValueError("address-book merges require explicit reviewed identity decisions")
            same_pairs.append((pair[0], pair[1], candidate.canonical_entity_id))
        elif candidate.semantic_decision == DisambiguationDecisionKind.DISTINCT_ENTITIES:
            distinct_pairs.add((pair[0], pair[1]))

    for left, right, _canonical in same_pairs:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)
    if any(find(left) == find(right) for left, right in distinct_pairs):
        raise ValueError("reviewed identity decisions contain a SAME/DISTINCT conflict")

    canonical_hints: dict[str, set[str]] = defaultdict(set)
    for left, _right, canonical in same_pairs:
        if canonical is not None:
            canonical_hints[find(left)].add(canonical)
    if any(len(hints) > 1 for hints in canonical_hints.values()):
        raise ValueError("reviewed identity links disagree on canonical contact ID")

    groups: dict[str, list[ContactIdentityObservation]] = defaultdict(list)
    for observation in items:
        groups[find(observation.entity_id)].append(observation)

    entries: list[AddressBookEntry] = []
    for root, group in groups.items():
        entity_ids = tuple(sorted(item.entity_id for item in group))
        hints = canonical_hints.get(root, set())
        contact_id = next(iter(hints)) if hints else entity_ids[0]
        claims = tuple(
            sorted(
                (
                    AddressBookClaim(
                        point=point,
                        stream_id=item.stream_id,
                        entity_id=item.entity_id,
                        source_document_ids=item.source_document_ids,
                        evidence_revision_ids=item.evidence_revision_ids,
                        observed_at_ms=item.observed_at_ms,
                        source_document_count=item.source_document_count,
                        evidence_revision_count=item.evidence_revision_count,
                        display_name_count=item.display_name_count,
                    )
                    for item in group
                    for point in item.contact_points
                ),
                key=lambda claim: (
                    claim.point.channel,
                    claim.point.provider or "",
                    claim.point.value,
                    claim.stream_id,
                    claim.entity_id,
                ),
            )
        )
        entries.append(
            AddressBookEntry(
                workspace_id=workspace_id,
                contact_id=contact_id,
                entity_ids=entity_ids,
                display_names=tuple(sorted({name for item in group for name in item.display_names})),
                contact_points=claims,
            )
        )
    return tuple(sorted(entries, key=lambda entry: entry.contact_id))


__all__ = [
    "AddressBookClaim",
    "AddressBookEntry",
    "ContactObservationProvider",
    "ContactScanObservationProvider",
    "build_address_book_projection",
    "compose_contact_observation_providers",
    "compose_contact_scan_observation_providers",
]
