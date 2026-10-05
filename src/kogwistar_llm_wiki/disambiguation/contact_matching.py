"""Conservative cross-channel contact-match candidate discovery.

This module proposes pairs only. It never merges entities, changes ACLs, or
persists raw contact values in candidate summaries or metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from itertools import combinations, islice
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .disambiguation_contracts import (
    DisambiguationCandidate,
    DisambiguationScoreBundle,
)

ContactChannel = str
ContactVerification = Literal["claimed", "provider_verified", "user_confirmed"]
DEFAULT_MAX_FUZZY_NAME_COMPARISONS = 100_000
_CHANNEL = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ContactPointClaim(BaseModel):
    """Channel-qualified contact-point claim supplied by an app adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: ContactChannel
    value: str = Field(min_length=1, max_length=512)
    provider: str | None = Field(default=None, max_length=128)
    verification: ContactVerification = "claimed"

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> ContactPointClaim:
        channel = str(self.channel).strip().casefold()
        value = str(self.value).strip()
        provider = str(self.provider or "").strip().casefold() or None
        if not _CHANNEL.fullmatch(channel):
            raise ValueError("contact channel must be a normalized identifier")
        if not value:
            raise ValueError("contact point value must not be empty")
        if _has_control(value) or (provider is not None and _has_control(provider)):
            raise ValueError("contact point values must not contain control characters")
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "provider", provider)
        return self


class ContactIdentityObservation(BaseModel):
    """ACL-scoped contact evidence with revisions covering its derivation profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = Field(min_length=1, max_length=256)
    stream_id: str = Field(min_length=1, max_length=256)
    entity_id: str = Field(min_length=1, max_length=512)
    source_document_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    evidence_revision_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    source_document_count: int | None = Field(default=None, strict=True, ge=1)
    evidence_revision_count: int | None = Field(default=None, strict=True, ge=1)
    observed_at_ms: int = Field(ge=0)
    display_names: tuple[str, ...] = Field(default=(), max_length=32)
    display_name_count: int | None = Field(default=None, strict=True, ge=0)
    evidence_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    contact_points: tuple[ContactPointClaim, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def _validate_identity(self) -> ContactIdentityObservation:
        for name in ("workspace_id", "stream_id", "entity_id"):
            value = str(getattr(self, name)).strip()
            if not value or _has_control(value):
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)
        documents = _clean_identifiers(self.source_document_ids, "source_document_ids")
        if not documents:
            raise ValueError("contact observation requires source document evidence")
        object.__setattr__(self, "source_document_ids", documents)
        source_document_count = self.source_document_count or len(documents)
        if source_document_count < len(documents):
            raise ValueError("source_document_count cannot be smaller than its sample")
        object.__setattr__(self, "source_document_count", source_document_count)
        revisions = _clean_identifiers(self.evidence_revision_ids, "evidence_revision_ids")
        if not revisions:
            raise ValueError("contact observation requires immutable evidence revision IDs")
        object.__setattr__(self, "evidence_revision_ids", revisions)
        evidence_revision_count = self.evidence_revision_count or len(revisions)
        if evidence_revision_count < len(revisions):
            raise ValueError("evidence_revision_count cannot be smaller than its sample")
        object.__setattr__(self, "evidence_revision_count", evidence_revision_count)
        names = tuple(dict.fromkeys(item.strip() for item in self.display_names if item.strip()))
        if any(len(name) > 256 or _has_control(name) for name in names):
            raise ValueError("display names exceed the bound or contain control characters")
        object.__setattr__(self, "display_names", names)
        display_name_count = (
            len(names) if self.display_name_count is None else self.display_name_count
        )
        if display_name_count < len(names):
            raise ValueError("display_name_count cannot be smaller than its sample")
        object.__setattr__(self, "display_name_count", display_name_count)
        if not names and not self.contact_points:
            raise ValueError("contact observation requires a name or contact point")
        if self.evidence_fingerprint is None and (
            source_document_count > len(documents)
            or evidence_revision_count > len(revisions)
            or display_name_count > len(names)
        ):
            raise ValueError("truncated contact evidence requires a full evidence_fingerprint")
        if self.evidence_fingerprint is None:
            object.__setattr__(
                self,
                "evidence_fingerprint",
                contact_observation_evidence_fingerprint(
                    source_document_ids=documents,
                    evidence_revision_ids=revisions,
                    display_names=names,
                    contact_points=self.contact_points,
                ),
            )
        return self


class AuthorizeContactStream(Protocol):
    """Authorize reading one contact source stream in a workspace."""

    def __call__(self, workspace_id: str, stream_id: str, /) -> bool: ...


def discover_contact_match_candidates(
    observations: Iterable[ContactIdentityObservation],
    *,
    authorize_stream: AuthorizeContactStream,
    max_observations: int = 250,
    max_candidates: int = 500,
    max_fuzzy_name_comparisons: int = DEFAULT_MAX_FUZZY_NAME_COMPARISONS,
    fuzzy_name_threshold: float = 0.92,
) -> tuple[DisambiguationCandidate, ...]:
    """Return deterministic, ACL-gated, review-only person-match candidates.

    Exact shared channel points outrank name evidence. Name-only candidates
    require at least two tokens; fuzzy matches are conservative and remain
    pending regardless of score. This function never decides SAME_ENTITY.
    """

    if not callable(authorize_stream):
        raise TypeError("authorize_stream must be callable")
    if (
        type(max_observations) is not int
        or type(max_candidates) is not int
        or type(max_fuzzy_name_comparisons) is not int
        or max_observations <= 0
        or max_candidates <= 0
        or max_fuzzy_name_comparisons <= 0
    ):
        raise ValueError("candidate bounds must be positive")
    if (
        isinstance(fuzzy_name_threshold, bool)
        or not isinstance(fuzzy_name_threshold, (int, float))
        or not 0.5 <= fuzzy_name_threshold <= 1.0
    ):
        raise ValueError("fuzzy_name_threshold must be between 0.5 and 1")

    items = tuple(islice(observations, max_observations + 1))
    if len(items) > max_observations:
        raise ValueError("contact observation batch exceeds max_observations")
    if not items:
        return ()
    workspaces = {item.workspace_id for item in items}
    if len(workspaces) != 1:
        raise ValueError("contact match scan cannot cross workspace boundaries")
    entity_ids = [item.entity_id for item in items]
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("contact match scan requires one aggregated observation per entity")

    workspace_id = next(iter(workspaces))
    for stream_id in sorted({item.stream_id for item in items}):
        if not authorize_stream(workspace_id, stream_id):
            raise PermissionError("contact match scan is not authorized for every source stream")

    ordered = sorted(items, key=lambda item: item.entity_id)
    candidates: dict[str, DisambiguationCandidate] = {}
    considered: set[tuple[int, int]] = set()

    def consider_pair(left_index: int, right_index: int) -> None:
        pair = (left_index, right_index)
        if left_index == right_index or pair in considered:
            return
        considered.add(pair)
        left, right = ordered[left_index], ordered[right_index]
        basis, similarity, channels, matched_channels, verification = _match_evidence(
            left, right, fuzzy_name_threshold
        )
        if basis is None:
            return
        candidate = _make_candidate(
            left,
            right,
            basis=basis,
            similarity=similarity,
            channels=channels,
            matched_channels=matched_channels,
            verification=verification,
        )
        candidates[candidate.candidate_key] = candidate
        if len(candidates) > max_candidates:
            raise ValueError("contact match scan exceeds max_candidates")

    # Exact shared contact points and exact names are indexed, avoiding a full
    # entity-by-entity scan for the common, high-signal matching cases.
    entities_by_point: dict[tuple[str, str, str], set[int]] = {}
    entities_by_name: dict[str, set[int]] = {}
    for index, observation in enumerate(ordered):
        for point in observation.contact_points:
            entities_by_point.setdefault(_point_key(point), set()).add(index)
        for name in observation.display_names:
            normalized = _normalize_name(name)
            if _is_matchable_name(normalized):
                entities_by_name.setdefault(normalized, set()).add(index)

    for indexes in entities_by_point.values():
        for left_index, right_index in combinations(sorted(indexes), 2):
            consider_pair(left_index, right_index)

    for indexes in entities_by_name.values():
        for left_index, right_index in combinations(sorted(indexes), 2):
            consider_pair(left_index, right_index)

    # Preserve the existing SequenceMatcher policy for fuzzy names, but skip
    # name pairs whose maximum possible ratio is already below threshold.
    # Its ratio is 2*M/(len(left)+len(right)), with M <= min(lengths).
    names = sorted(entities_by_name)
    fuzzy_name_comparisons = 0
    for left_position, left_name in enumerate(names):
        left_length = len(left_name)
        for right_position in range(left_position + 1, len(names)):
            right_name = names[right_position]
            right_length = len(right_name)
            maximum_ratio = 2 * min(left_length, right_length) / (
                left_length + right_length
            )
            if maximum_ratio < fuzzy_name_threshold:
                continue
            fuzzy_name_comparisons += 1
            if fuzzy_name_comparisons > max_fuzzy_name_comparisons:
                raise ValueError(
                    "contact match scan exceeds max_fuzzy_name_comparisons"
                )
            if (
                SequenceMatcher(None, left_name, right_name).ratio()
                < fuzzy_name_threshold
                and SequenceMatcher(None, right_name, left_name).ratio()
                < fuzzy_name_threshold
            ):
                continue
            for left_index in sorted(entities_by_name[left_name]):
                for right_index in sorted(entities_by_name[right_name]):
                    if left_index < right_index:
                        consider_pair(left_index, right_index)
                    else:
                        consider_pair(right_index, left_index)
    return tuple(candidates[key] for key in sorted(candidates))


def contact_match_basis(
    left: ContactIdentityObservation,
    right: ContactIdentityObservation,
    *,
    fuzzy_name_threshold: float = 0.92,
) -> str | None:
    """Return current matching basis for one pair, without creating a candidate."""

    if left.workspace_id != right.workspace_id:
        raise ValueError("contact match evidence cannot cross workspace boundaries")
    if (
        isinstance(fuzzy_name_threshold, bool)
        or not isinstance(fuzzy_name_threshold, (int, float))
        or not 0.5 <= fuzzy_name_threshold <= 1.0
    ):
        raise ValueError("fuzzy_name_threshold must be between 0.5 and 1")
    return _match_evidence(left, right, float(fuzzy_name_threshold))[0]


def _match_evidence(
    left: ContactIdentityObservation,
    right: ContactIdentityObservation,
    fuzzy_name_threshold: float,
) -> tuple[
    str | None,
    float,
    tuple[str, ...],
    tuple[str, ...],
    ContactVerification | None,
]:
    trust_rank = {"claimed": 0, "provider_verified": 1, "user_confirmed": 2}

    def strongest_claims(
        observation: ContactIdentityObservation,
    ) -> dict[tuple[str, str, str], int]:
        claims: dict[tuple[str, str, str], int] = {}
        for point in observation.contact_points:
            key = _point_key(point)
            claims[key] = max(claims.get(key, 0), trust_rank[point.verification])
        return claims

    left_claims = strongest_claims(left)
    right_claims = strongest_claims(right)
    shared_keys = left_claims.keys() & right_claims.keys()
    observed_channels = tuple(
        sorted({point.channel for point in (*left.contact_points, *right.contact_points)})
    )
    if shared_keys:
        matched_channels = tuple(sorted({key[0] for key in shared_keys}))
        verification = max(
            (min(left_claims[key], right_claims[key]) for key in shared_keys),
            default=0,
        )
        verification_name: ContactVerification = (
            "user_confirmed" if verification == 2 else
            "provider_verified" if verification == 1 else
            "claimed"
        )
        return "shared_contact_point", 1.0, observed_channels, matched_channels, verification_name

    best_similarity = 0.0
    for left_name in left.display_names:
        left_normalized = _normalize_name(left_name)
        if not _is_matchable_name(left_normalized):
            continue
        for right_name in right.display_names:
            right_normalized = _normalize_name(right_name)
            if not _is_matchable_name(right_normalized):
                continue
            similarity = SequenceMatcher(None, left_normalized, right_normalized).ratio()
            best_similarity = max(best_similarity, similarity)
    if best_similarity == 1.0:
        return "exact_name", best_similarity, observed_channels, (), None
    if best_similarity >= fuzzy_name_threshold:
        return "similar_name", best_similarity, observed_channels, (), None
    return None, 0.0, (), (), None


def _make_candidate(
    left: ContactIdentityObservation,
    right: ContactIdentityObservation,
    *,
    basis: str,
    similarity: float,
    channels: tuple[str, ...],
    matched_channels: tuple[str, ...],
    verification: ContactVerification | None,
) -> DisambiguationCandidate:
    entity_ids = tuple(sorted((left.entity_id, right.entity_id)))
    source_document_ids = tuple(sorted(set(left.source_document_ids + right.source_document_ids)))
    identity = json.dumps(
        {"workspace": left.workspace_id, "entities": entity_ids},
        sort_keys=True,
        separators=(",", ":"),
    )
    candidate_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    evidence_snapshot_id = contact_evidence_snapshot_id(left, right, basis=basis)

    if basis == "shared_contact_point":
        likelihood = {
            "claimed": 0.82,
            "provider_verified": 0.90,
            "user_confirmed": 0.96,
        }[verification or "claimed"]
        priority, answer_risk = 0.9, 0.9
        summary = "A channel-qualified contact point is shared across source observations; person identity still requires review."
    elif basis == "exact_name":
        likelihood, priority, answer_risk = 0.58, 0.58, 0.62
        summary = "Names match across source observations; no shared contact point was found."
    else:
        likelihood = min(0.72, 0.45 + 0.20 * similarity)
        priority = min(0.68, 0.45 + 0.16 * similarity)
        answer_risk = 0.52
        summary = "Names are similar across source observations; no shared contact point was found."

    left_document_count = left.source_document_count or len(left.source_document_ids)
    right_document_count = right.source_document_count or len(right.source_document_ids)

    return DisambiguationCandidate(
        artifact_id=f"contact-match:{candidate_digest}",
        workspace_id=left.workspace_id,
        candidate_key=f"contact-match:{candidate_digest}",
        entity_ids=entity_ids,
        evidence_snapshot_id=evidence_snapshot_id,
        evidence_cutoff_ms=max(left.observed_at_ms, right.observed_at_ms),
        question="Could these contact records refer to the same person?",
        question_is_concrete=True,
        evidence_summary=summary,
        source_document_ids=source_document_ids,
        score_bundle=DisambiguationScoreBundle(
            merge_likelihood=likelihood,
            distinction_pressure=0.25 if basis == "shared_contact_point" else 0.55,
            review_priority=priority,
            usage_frequency=left_document_count + right_document_count,
            answer_risk=answer_risk,
        ),
        metadata={
            "match_basis": basis,
            "channels": ",".join(channels),
            "matched_channels": ",".join(matched_channels),
            "source_stream_ids": json.dumps(
                sorted({left.stream_id, right.stream_id}), separators=(",", ":")
            ),
            "name_similarity": round(similarity, 4),
            "contact_point_verification": verification,
            "source_document_observation_count": (
                left_document_count + right_document_count
            ),
            "source_document_sampled": (
                len(left.source_document_ids) < left_document_count
                or len(right.source_document_ids) < right_document_count
            ),
            "automatic_merge": False,
        },
    )


def contact_evidence_snapshot_id(
    left: ContactIdentityObservation,
    right: ContactIdentityObservation,
    *,
    basis: str,
) -> str:
    """Fingerprint all source evidence used for a two-contact comparison."""

    if left.workspace_id != right.workspace_id or left.entity_id == right.entity_id:
        raise ValueError("contact evidence snapshot requires two distinct same-workspace entities")
    evidence_snapshot = json.dumps(
        {
            "workspace_id": left.workspace_id,
            "entities": tuple(sorted((left.entity_id, right.entity_id))),
            "source_documents": sorted(set(left.source_document_ids + right.source_document_ids)),
            "evidence_revisions": sorted(
                set(left.evidence_revision_ids + right.evidence_revision_ids)
            ),
            "observed_at_ms": sorted((left.observed_at_ms, right.observed_at_ms)),
            "observations": [
                _snapshot_observation(item)
                for item in sorted((left, right), key=lambda observation: observation.entity_id)
            ],
            "basis": basis,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(evidence_snapshot.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _snapshot_observation(item: ContactIdentityObservation) -> dict[str, object]:
    return {
        "entity_id": item.entity_id,
        "stream_id": item.stream_id,
        "source_documents": sorted(item.source_document_ids),
        "source_document_count": item.source_document_count,
        "evidence_revisions": sorted(item.evidence_revision_ids),
        "evidence_revision_count": item.evidence_revision_count,
        "evidence_fingerprint": item.evidence_fingerprint,
        "observed_at_ms": item.observed_at_ms,
        "display_names": sorted(item.display_names),
        "display_name_count": item.display_name_count,
        "contact_points": sorted(
            (
                point.channel,
                point.provider or "",
                point.value,
                point.verification,
            )
            for point in item.contact_points
        ),
    }


def contact_observation_evidence_fingerprint(
    *,
    source_document_ids: Iterable[str],
    evidence_revision_ids: Iterable[str],
    display_names: Iterable[str],
    contact_points: Iterable[ContactPointClaim],
    display_name_last_seen_ms: Mapping[str, int] | None = None,
) -> str:
    """Hash the complete derivation input, including values omitted from UI samples."""

    payload = {
        "source_documents": sorted(set(source_document_ids)),
        "evidence_revisions": sorted(set(evidence_revision_ids)),
        "display_names": sorted(display_names),
        "display_name_last_seen_ms": dict(sorted((display_name_last_seen_ms or {}).items())),
        "contact_points": sorted(
            (
                point.channel,
                point.provider or "",
                point.value,
                point.verification,
            )
            for point in contact_points
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _point_key(point: ContactPointClaim) -> tuple[str, str, str]:
    return point.channel, point.provider or "", point.value


def _clean_identifiers(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if not item or len(item) > 512 or _has_control(item):
            raise ValueError(f"{name} contains an invalid identifier")
        if item not in cleaned:
            cleaned.append(item)
    return tuple(cleaned)


def _has_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _is_matchable_name(value: str) -> bool:
    if len(value.split()) >= 2:
        return True
    letters = tuple(character for character in value if character.isalpha())
    if len(letters) < 2:
        return False
    east_asian_prefixes = (
        "BOPOMOFO",
        "CJK UNIFIED IDEOGRAPH",
        "HIRAGANA",
        "KATAKANA",
        "HANGUL",
    )
    return any(
        unicodedata.name(character, "").startswith(east_asian_prefixes)
        for character in letters
    )


__all__ = [
    "ContactChannel",
    "ContactIdentityObservation",
    "ContactPointClaim",
    "ContactVerification",
    "contact_evidence_snapshot_id",
    "contact_match_basis",
    "discover_contact_match_candidates",
]
