"""Immutable parse evidence and per-source active interpretation pointers.

The parser may produce many generations for one logical source.  This module
keeps those event identities separate from the mutable active view pointer so
readers never infer the active interpretation from insertion order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import pairwise
from typing import Any

from kogwistar.engine_core import NamedProjectionStore
from kogwistar.id_provider import stable_id
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParseGenerationStatus(StrEnum):
    SEEDED = "seeded"
    EXPANDING = "expanding"
    STABLE = "stable"
    FAILED = "failed"


class ParseSessionPhase(StrEnum):
    SEEDED = "parse_seeded"
    EXPANDING = "parse_expanding"
    STABLE = "parsed_graph_persisted"
    FAILED = "failed"


class ParseFrontierStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETE = "complete"
    FAILED = "failed"


class ParseViewStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class SourceRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_document_id: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> SourceRegion:
        if self.end_char <= self.start_char:
            raise ValueError("source region end_char must be greater than start_char")
        return self


class ParseTarget(BaseModel):
    """A revision-pinned, bounded region selected for an explicit reparse."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_document_id: str
    source_revision_id: str
    revision_document_id: str
    region: SourceRegion
    reason: str = Field(min_length=1, max_length=512)
    parser_profile: str = Field(min_length=1, max_length=256)
    generation_member_id: str | None = None
    llm_provider: str | None = Field(default=None, max_length=128)
    llm_model: str | None = Field(default=None, max_length=256)
    model_version: str | None = Field(default=None, max_length=256)
    prompt_version: str | None = Field(default=None, max_length=256)
    parser_version: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _region_is_pinned_to_revision(self) -> ParseTarget:
        if self.region.source_document_id != self.revision_document_id:
            raise ValueError("parse target region must point to its revision document")
        return self


class ParseGeneration(BaseModel):
    """Immutable identity and provenance of one parser derivation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_id: str
    workspace_id: str
    source_document_id: str
    source_revision_id: str
    source_digest: str
    revision_document_id: str
    parser_profile: str
    parser_version: str
    llm_provider: str | None = None
    llm_model: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    parent_generation_id: str | None = None
    maintenance_run_id: str | None = None
    status: ParseGenerationStatus = ParseGenerationStatus.SEEDED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ParseGenerationCommit(BaseModel):
    """Immutable idempotent commit event for generation members."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    commit_id: str
    generation_id: str
    workspace_id: str
    source_document_id: str
    source_revision_id: str
    member_ids: tuple[str, ...] = ()
    consumed_frontier_ids: tuple[str, ...] = ()
    attempt: int = Field(default=1, ge=1)
    committed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ParseGenerationMember(BaseModel):
    """Immutable evidence member; semantic IDs remain references, not versions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    member_id: str
    generation_id: str
    workspace_id: str
    source_document_id: str
    source_revision_id: str
    revision_document_id: str
    region: SourceRegion
    semantic_id: str | None = None
    semantic_fingerprint: str | None = None
    parent_member_id: str | None = None
    depth: int = Field(default=0, ge=0)
    payload: Mapping[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _region_is_pinned_to_revision(self) -> ParseGenerationMember:
        if self.region.source_document_id != self.revision_document_id:
            raise ValueError("parse generation member region must point to its revision document")
        return self


class ParseFrontierItem(BaseModel):
    """Durable bounded work item; status changes are session state, not evidence."""

    model_config = ConfigDict(extra="forbid")

    frontier_id: str
    session_id: str
    generation_id: str
    workspace_id: str
    source_document_id: str
    source_revision_id: str
    revision_document_id: str
    parent_member_id: str | None = None
    region: SourceRegion
    depth: int = Field(default=0, ge=0)
    ordinal: int = Field(default=0, ge=0)
    status: ParseFrontierStatus = ParseFrontierStatus.PENDING
    attempt: int = Field(default=0, ge=0)
    last_error: str | None = None


class ParseSessionState(BaseModel):
    """Restartable parser session state owned by LLM-Wiki."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    workspace_id: str
    source_document_id: str
    source_revision_id: str
    source_digest: str
    revision_document_id: str
    generation_id: str
    phase: ParseSessionPhase = ParseSessionPhase.SEEDED
    frontier_ids: tuple[str, ...] = ()
    consumed_frontier_ids: tuple[str, ...] = ()
    max_depth: int = Field(default=0, ge=0)
    max_frontier_items: int = Field(default=1, ge=1)
    max_parser_calls: int = Field(default=1000, ge=1)
    max_region_chars: int = Field(default=16_384, ge=1)
    parser_calls: int = Field(default=0, ge=0)
    token_budget: int | None = Field(default=None, ge=1)
    wall_time_seconds: float | None = Field(default=None, gt=0)
    last_progress_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    failure_reason: str | None = None
    # Inputs are revision-pinned and JSON-safe; raw bytes remain in the source document.
    parser_state: dict[str, Any] = Field(default_factory=dict)
    # A view activation is a two-phase operation. Keeping the proposed view in
    # the session makes a crash between session CAS and view CAS recoverable.
    pending_view: dict[str, Any] | None = None


class ParseViewSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    member_id: str
    generation_id: str
    region: SourceRegion


class ParseView(BaseModel):
    """A complete active interpretation for one logical source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    view_id: str
    view_version: int = Field(ge=1)
    workspace_id: str
    source_document_id: str
    source_revision_id: str
    revision_document_id: str
    selections: tuple[ParseViewSelection, ...] = ()
    predecessor_view_id: str | None = None
    status: ParseViewStatus = ParseViewStatus.ACTIVE
    activated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _selections_are_grounded_and_disjoint(self) -> ParseView:
        regions = sorted(
            self.selections,
            key=lambda selection: (
                selection.region.start_char,
                selection.region.end_char,
                selection.member_id,
            ),
        )
        for selection in regions:
            if selection.region.source_document_id != self.revision_document_id:
                raise ValueError("ParseView selections must point to the revision document")
        for previous, current in pairwise(regions):
            if current.region.start_char < previous.region.end_char:
                raise ValueError("ParseView selections must not overlap")
        return self


def generation_id(
    *,
    workspace_id: str,
    source_document_id: str,
    source_revision_id: str,
    parser_profile: str,
    derivation_id: str,
) -> str:
    """Return the idempotency identity of one parser derivation.

    A source revision and parser profile describe compatibility, but are not an
    event identity: a targeted reparse can use the same profile for a distinct
    region.  The caller therefore supplies the durable parse-session identity
    as the derivation scope.
    """

    return str(
        stable_id(
            "kogwistar_llm_wiki.parse_generation",
            workspace_id,
            source_document_id,
            source_revision_id,
            parser_profile,
            derivation_id,
        )
    )


def parse_session_id(
    *,
    workspace_id: str,
    source_document_id: str,
    source_revision_id: str,
    parser_profile: str,
) -> str:
    """Return the durable identity for a full-source parse derivation."""

    return str(
        stable_id(
            "kogwistar_llm_wiki.parse_session",
            workspace_id,
            source_document_id,
            source_revision_id,
            parser_profile,
        )
    )


def reparse_session_id(
    *,
    workspace_id: str,
    source_document_id: str,
    source_revision_id: str,
    parser_profile: str,
    region: SourceRegion,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
    parser_version: str | None = None,
) -> str:
    """Return a distinct idempotency scope for one explicit region reparse."""

    return str(
        stable_id(
            "kogwistar_llm_wiki.reparse_session",
            workspace_id,
            source_document_id,
            source_revision_id,
            parser_profile,
            region.source_document_id,
            str(region.start_char),
            str(region.end_char),
            llm_provider or "",
            llm_model or "",
            model_version or "",
            prompt_version or "",
            parser_version or "",
        )
    )


def generation_member_id(*, generation_id: str, commit_id: str, ordinal: int) -> str:
    return str(stable_id("kogwistar_llm_wiki.parse_generation_member", generation_id, commit_id, str(ordinal)))


def frontier_id(*, session_id: str, region: SourceRegion, ordinal: int) -> str:
    return str(
        stable_id(
            "kogwistar_llm_wiki.parse_frontier",
            session_id,
            region.source_document_id,
            str(region.start_char),
            str(region.end_char),
            str(ordinal),
        )
    )


def legacy_generation_id(*, workspace_id: str, source_document_id: str) -> str:
    return str(stable_id("kogwistar_llm_wiki.parse_generation.legacy_g0", workspace_id, source_document_id))


class ParseViewConflict(RuntimeError):
    """Raised when another writer activated a source view first."""


class ParseViewStore:
    """CAS-backed per-source ParseView pointers.

    The projection key is source-scoped.  Its metadata sequence is used only
    as a CAS token; it is not a global application ordering.
    """

    __slots__ = ("metadata", "workspace_id")
    schema_version = 1

    def __init__(self, metadata: NamedProjectionStore, *, workspace_id: str) -> None:
        self.metadata = metadata
        self.workspace_id = workspace_id

    def key(self, source_document_id: str) -> str:
        return f"parse_view:{source_document_id}"

    def get(self, source_document_id: str) -> ParseView | None:
        row = self.metadata.get_named_projection(
            f"ws:{self.workspace_id}:projection_state",
            self.key(source_document_id),
        )
        if row is None:
            return None
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            raise TypeError("parse view projection payload must be an object")
        view = ParseView.model_validate(payload)
        if view.workspace_id != self.workspace_id:
            raise ValueError("stored ParseView workspace does not match store workspace")
        if view.source_document_id != source_document_id:
            raise ValueError("stored ParseView source does not match projection key")
        self._validate_view_evidence(view)
        return view

    def activate(self, view: ParseView, *, expected_view_version: int | None) -> bool:
        if view.workspace_id != self.workspace_id:
            raise ValueError("ParseView workspace does not match store workspace")
        if view.view_version < 1:
            raise ValueError("ParseView version must be positive")
        self._validate_view_evidence(view)
        key = self.key(view.source_document_id)
        namespace = f"ws:{self.workspace_id}:projection_state"
        existing = self.metadata.get_named_projection(namespace, key)
        if expected_view_version is None:
            if existing is not None:
                raise ParseViewConflict("expected no existing ParseView")
            expected_authoritative = None
            expected_materialized = None
        else:
            if existing is None:
                raise ParseViewConflict("expected an existing ParseView")
            current = ParseView.model_validate(existing.get("payload", {}))
            self._validate_view_evidence(current)
            if current.source_document_id != view.source_document_id:
                raise ValueError("stored ParseView source does not match replacement source")
            if current.view_version != expected_view_version:
                raise ParseViewConflict("ParseView version changed before activation")
            if view.view_version <= current.view_version:
                raise ValueError("ParseView version must increase when replacing an active view")
            expected_authoritative = int(existing.get("last_authoritative_seq", -1))
            expected_materialized = int(existing.get("last_materialized_seq", -1))
        inserted = self.metadata.compare_and_swap_named_projection(
            namespace,
            key,
            view.model_dump(mode="json"),
            expected_last_authoritative_seq=expected_authoritative,
            expected_last_materialized_seq=expected_materialized,
            last_authoritative_seq=view.view_version,
            last_materialized_seq=view.view_version,
            projection_schema_version=self.schema_version,
            materialization_status="ready",
        )
        if not inserted:
            raise ParseViewConflict("ParseView activation lost a compare-and-swap race")
        return True

    def _validate_view_evidence(self, view: ParseView) -> None:
        """Ensure every selected member is committed evidence in this scope."""

        namespace = f"ws:{self.workspace_id}:projection_state"
        for selection in view.selections:
            row = self.metadata.get_named_projection(
                namespace,
                f"parse_generation:{selection.generation_id}",
            )
            if row is None or not isinstance(row.get("payload"), Mapping):
                raise ValueError("ParseView selection references an unknown generation")
            payload = row["payload"]
            generation_payload = payload.get("generation")
            members = payload.get("members")
            if not isinstance(generation_payload, Mapping) or not isinstance(members, Mapping):
                raise TypeError("ParseView selection references malformed generation evidence")
            generation = ParseGeneration.model_validate(generation_payload)
            if (
                generation.workspace_id != self.workspace_id
                or generation.generation_id != selection.generation_id
                or generation.source_document_id != view.source_document_id
                or generation.source_revision_id != view.source_revision_id
                or generation.revision_document_id != view.revision_document_id
                or generation.status != ParseGenerationStatus.STABLE
            ):
                raise ValueError("ParseView selection is outside the active source generation")
            commits = payload.get("commits")
            if not isinstance(commits, Mapping):
                raise TypeError("ParseView selection references missing generation commits")
            committed_member_ids: set[str] = set()
            for commit_payload in commits.values():
                if not isinstance(commit_payload, Mapping):
                    raise TypeError("ParseView selection references malformed generation commit")
                commit = ParseGenerationCommit.model_validate(commit_payload)
                if (
                    commit.workspace_id != self.workspace_id
                    or commit.generation_id != generation.generation_id
                    or commit.source_document_id != generation.source_document_id
                    or commit.source_revision_id != generation.source_revision_id
                ):
                    raise ValueError("ParseView selection references a foreign generation commit")
                committed_member_ids.update(commit.member_ids)
            member_payload = members.get(selection.member_id)
            if not isinstance(member_payload, Mapping):
                raise TypeError("ParseView selection references an unknown generation member")
            member = ParseGenerationMember.model_validate(member_payload)
            if (
                member.workspace_id != self.workspace_id
                or member.generation_id != selection.generation_id
                or member.member_id != selection.member_id
                or member.source_document_id != view.source_document_id
                or member.source_revision_id != view.source_revision_id
                or member.revision_document_id != view.revision_document_id
                or member.region != selection.region
            ):
                raise ValueError("ParseView selection is not grounded in its generation member")
            if selection.member_id not in committed_member_ids:
                raise ValueError("ParseView selection references an uncommitted generation member")


@dataclass(frozen=True, slots=True)
class ParseViewResolution:
    """The active interpretation selected for one logical source."""

    source_document_id: str
    revision_document_id: str
    view_id: str | None
    view_version: int | None
    generation_ids: tuple[str, ...]
    member_ids: tuple[str, ...]
    is_legacy: bool


class ParseViewResolver:
    """Resolve active source data without relying on insertion order.

    A missing pointer is treated as virtual legacy ``G0``.  The resolver does
    not mutate that legacy state; callers may use the returned fallback
    revision for read-only compatibility while new ParseViews are committed.
    """

    __slots__ = ("store", "workspace_id")

    def __init__(self, metadata: NamedProjectionStore, *, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.store = ParseViewStore(metadata, workspace_id=workspace_id)

    def resolve(
        self,
        source_document_id: str,
        *,
        fallback_revision_document_id: str | None = None,
    ) -> ParseViewResolution:
        view = self.store.get(source_document_id)
        if view is not None:
            return ParseViewResolution(
                source_document_id=source_document_id,
                revision_document_id=view.revision_document_id,
                view_id=view.view_id,
                view_version=view.view_version,
                generation_ids=tuple(sorted({item.generation_id for item in view.selections})),
                member_ids=tuple(item.member_id for item in view.selections),
                is_legacy=False,
            )
        return ParseViewResolution(
            source_document_id=source_document_id,
            revision_document_id=str(fallback_revision_document_id or ""),
            view_id=None,
            view_version=None,
            generation_ids=(legacy_generation_id(workspace_id=self.workspace_id, source_document_id=source_document_id),),
            member_ids=(),
            is_legacy=True,
        )

    def is_active_revision(
        self,
        source_document_id: str,
        revision_document_id: str,
        *,
        fallback_revision_document_id: str | None = None,
    ) -> bool:
        resolution = self.resolve(
            source_document_id,
            fallback_revision_document_id=fallback_revision_document_id,
        )
        return bool(resolution.revision_document_id) and resolution.revision_document_id == revision_document_id

    def is_active_metadata(
        self,
        source_document_id: str,
        metadata: Mapping[str, Any],
        *,
        fallback_revision_document_id: str | None = None,
    ) -> bool:
        """Check the revision/member identity carried by a graph artifact."""

        resolution = self.resolve(
            source_document_id,
            fallback_revision_document_id=fallback_revision_document_id,
        )
        artifact_revision = str(
            metadata.get("source_revision_document_id")
            or metadata.get("revision_document_id")
            or ""
        ).strip()
        if artifact_revision and resolution.revision_document_id and artifact_revision != resolution.revision_document_id:
            return False
        member_id = str(
            metadata.get("parse_generation_member_id")
            or metadata.get("generation_member_id")
            or metadata.get("member_id")
            or ""
        ).strip()
        # New derivations are written before their ParseView can be activated.
        # Keep them invisible during that crash window and require a selected
        # member ID once a view exists.
        if resolution.member_ids:
            return bool(member_id) and member_id in resolution.member_ids
        return not member_id


def validate_parse_view_selections(
    selections: Sequence[ParseViewSelection], *, revision_document_id: str
) -> None:
    ParseView(
        view_id="validation",
        view_version=1,
        workspace_id="validation",
        source_document_id="validation",
        source_revision_id="validation",
        revision_document_id=revision_document_id,
        selections=tuple(selections),
    )


__all__ = [
    "ParseFrontierItem",
    "ParseFrontierStatus",
    "ParseGeneration",
    "ParseGenerationCommit",
    "ParseGenerationMember",
    "ParseSessionPhase",
    "ParseSessionState",
    "ParseTarget",
    "ParseView",
    "ParseViewConflict",
    "ParseViewResolution",
    "ParseViewResolver",
    "ParseViewSelection",
    "ParseViewStatus",
    "ParseViewStore",
    "SourceRegion",
    "frontier_id",
    "generation_id",
    "generation_member_id",
    "legacy_generation_id",
    "parse_session_id",
    "reparse_session_id",
    "validate_parse_view_selections",
]
