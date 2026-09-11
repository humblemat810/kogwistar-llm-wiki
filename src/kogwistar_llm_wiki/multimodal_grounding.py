"""Typed higher-order grounding contracts for the multimodal application plane.

Kogwistar's existing ``Grounding`` model remains span-based.  This module does
not replace it or write graph records directly; it provides the additive,
serializable contract that a multimodal proposal/persistence adapter can use
to validate an evidence-pack reference before an authoritative write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Literal, Protocol

from kogwistar.logical_refs import LogicalRef


GroundingComposition = Literal["all_of", "any_of"]
EntityKind = Literal["node", "edge"]


class GroundingValidationError(ValueError):
    """Raised when a higher-order grounding cannot be proven safely."""


def _mapping_sequence(
    payload: Mapping[str, object], key: str, *, required: bool = False
) -> tuple[Mapping[str, object], ...]:
    raw = payload.get(key)
    if raw is None:
        if required:
            raise ValueError(f"evidence payload requires {key}")
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError(f"evidence payload field {key!r} must be a sequence")
    if not all(isinstance(item, Mapping) for item in raw):
        raise ValueError(f"evidence payload field {key!r} entries must be mappings")
    return tuple(item for item in raw if isinstance(item, Mapping))


@dataclass(frozen=True, slots=True)
class SourceEvidenceRef:
    """A verified source-map reference at one immutable revision."""

    workspace_id: str
    source_id: str
    source_revision_id: str
    source_unit_id: str
    locator_kind: str
    locator_digest: str
    role: str = "evidence"

    def __post_init__(self) -> None:
        if not all(
            str(value).strip()
            for value in (
                self.workspace_id,
                self.source_id,
                self.source_revision_id,
                self.source_unit_id,
                self.locator_kind,
                self.locator_digest,
            )
        ):
            raise ValueError("source evidence requires complete revision and locator identity")

    def to_payload(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "source_id": self.source_id,
            "source_revision_id": self.source_revision_id,
            "source_unit_id": self.source_unit_id,
            "locator_kind": self.locator_kind,
            "locator_digest": self.locator_digest,
            "role": self.role,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "SourceEvidenceRef":
        return cls(
            workspace_id=str(payload["workspace_id"]),
            source_id=str(payload["source_id"]),
            source_revision_id=str(payload["source_revision_id"]),
            source_unit_id=str(payload["source_unit_id"]),
            locator_kind=str(payload["locator_kind"]),
            locator_digest=str(payload["locator_digest"]),
            role=str(payload.get("role", "evidence")),
        )


@dataclass(frozen=True, slots=True)
class PinnedEntityRef:
    """A typed node/edge reference pinned to an event/revision watermark."""

    logical_ref: LogicalRef
    revision_id: str
    event_seq: int
    role: str = "support"

    def __post_init__(self) -> None:
        if self.logical_ref.target_kind not in {"node", "edge"}:
            raise ValueError("pinned evidence entities must target a node or edge")
        if not self.revision_id or self.event_seq < 0:
            raise ValueError("pinned evidence entities require a revision and non-negative event_seq")

    @property
    def entity_kind(self) -> EntityKind:
        return self.logical_ref.target_kind  # type: ignore[return-value]

    def to_payload(self) -> dict[str, object]:
        return {
            "logical_ref": {
                "target_namespace": self.logical_ref.target_namespace,
                "target_kind": self.logical_ref.target_kind,
                "target_id": self.logical_ref.target_id,
            },
            "revision_id": self.revision_id,
            "event_seq": self.event_seq,
            "role": self.role,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "PinnedEntityRef":
        raw_ref = payload.get("logical_ref")
        if not isinstance(raw_ref, Mapping):
            raise ValueError("pinned evidence entity requires logical_ref")
        return cls(
            logical_ref=LogicalRef(
                target_namespace=str(raw_ref["target_namespace"]),
                target_kind=str(raw_ref["target_kind"]),  # type: ignore[arg-type]
                target_id=str(raw_ref["target_id"]),
            ),
            revision_id=str(payload["revision_id"]),
            event_seq=int(payload["event_seq"]),
            role=str(payload.get("role", "support")),
        )


@dataclass(frozen=True, slots=True)
class EvidencePackReference:
    """A reference to a durable evidence-pack artifact and its expected hash."""

    pack_ref: LogicalRef
    expected_hash: str
    source_watermark: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.pack_ref.target_kind not in {"node", "artifact"}:
            raise ValueError("evidence packs must target a node or artifact")
        if not self.expected_hash:
            raise ValueError("evidence packs require an expected hash")
        if any(int(value) < 0 for value in self.source_watermark.values()):
            raise ValueError("source watermarks must be non-negative")

    def to_payload(self) -> dict[str, object]:
        return {
            "pack_ref": {
                "target_namespace": self.pack_ref.target_namespace,
                "target_kind": self.pack_ref.target_kind,
                "target_id": self.pack_ref.target_id,
            },
            "expected_hash": self.expected_hash,
            "source_watermark": {str(key): int(value) for key, value in self.source_watermark.items()},
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "EvidencePackReference":
        raw_ref = payload.get("pack_ref")
        if not isinstance(raw_ref, Mapping):
            raise ValueError("evidence pack reference requires pack_ref")
        raw_watermark = payload.get("source_watermark") or {}
        if not isinstance(raw_watermark, Mapping):
            raise ValueError("evidence pack source_watermark must be a mapping")
        return cls(
            pack_ref=LogicalRef(
                target_namespace=str(raw_ref["target_namespace"]),
                target_kind=str(raw_ref["target_kind"]),  # type: ignore[arg-type]
                target_id=str(raw_ref["target_id"]),
            ),
            expected_hash=str(payload["expected_hash"]),
            source_watermark={str(key): int(value) for key, value in raw_watermark.items()},
        )


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Canonical evidence pack containing typed graph and direct-source refs."""

    pack_id: str
    namespace: str
    node_refs: tuple[PinnedEntityRef, ...] = ()
    edge_refs: tuple[PinnedEntityRef, ...] = ()
    source_refs: tuple[SourceEvidenceRef, ...] = ()
    source_watermarks: Mapping[str, int] = field(default_factory=dict)
    composition: GroundingComposition = "all_of"
    source_closure_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.pack_id or not self.namespace:
            raise ValueError("evidence packs require pack_id and namespace")
        if self.composition not in {"all_of", "any_of"}:
            raise ValueError(f"unsupported evidence composition {self.composition!r}")
        refs = self.node_refs + self.edge_refs
        identities = [
            (
                ref.logical_ref.target_namespace,
                ref.logical_ref.target_kind,
                ref.logical_ref.target_id,
                ref.revision_id,
            )
            for ref in refs
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("evidence pack contains duplicate typed entity references")
        if any(ref.logical_ref.target_namespace != self.namespace for ref in refs):
            raise ValueError("evidence pack entity references must use its namespace")
        if any(int(value) < 0 for value in self.source_watermarks.values()):
            raise ValueError("source watermarks must be non-negative")

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(ref.logical_ref.target_id for ref in self.node_refs)

    @property
    def edge_ids(self) -> tuple[str, ...]:
        return tuple(ref.logical_ref.target_id for ref in self.edge_refs)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "namespace": self.namespace,
            "node_refs": [ref.to_payload() for ref in self.node_refs],
            "edge_refs": [ref.to_payload() for ref in self.edge_refs],
            "source_refs": [ref.to_payload() for ref in self.source_refs],
            "source_watermarks": {str(key): int(value) for key, value in sorted(self.source_watermarks.items())},
            "composition": self.composition,
        }

    @property
    def content_hash(self) -> str:
        blob = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return sha256(blob).hexdigest()

    def to_payload(self) -> dict[str, object]:
        payload = self.canonical_payload()
        payload["source_closure_hash"] = self.source_closure_hash or self.content_hash
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "EvidencePack":
        raw_nodes = _mapping_sequence(payload, "node_refs")
        raw_edges = _mapping_sequence(payload, "edge_refs")
        raw_sources = _mapping_sequence(payload, "source_refs")
        raw_watermarks = payload.get("source_watermarks") or {}
        if not isinstance(raw_watermarks, Mapping):
            raise ValueError("evidence pack source_watermarks must be a mapping")
        return cls(
            pack_id=str(payload["pack_id"]),
            namespace=str(payload["namespace"]),
            node_refs=tuple(PinnedEntityRef.from_payload(item) for item in raw_nodes),
            edge_refs=tuple(PinnedEntityRef.from_payload(item) for item in raw_edges),
            source_refs=tuple(SourceEvidenceRef.from_payload(item) for item in raw_sources),
            source_watermarks={str(key): int(value) for key, value in raw_watermarks.items()},
            composition=str(payload.get("composition", "all_of")),  # type: ignore[arg-type]
            source_closure_hash=str(payload["source_closure_hash"]) if payload.get("source_closure_hash") else None,
        )


@dataclass(frozen=True, slots=True)
class HigherOrderGrounding:
    """Grounding attached to a node or edge without mixing topology endpoints."""

    evidence_pack_refs: tuple[EvidencePackReference, ...]

    def __post_init__(self) -> None:
        if not self.evidence_pack_refs:
            raise ValueError("higher-order grounding requires an evidence pack reference")

    def to_payload(self) -> dict[str, object]:
        return {"evidence_pack_refs": [ref.to_payload() for ref in self.evidence_pack_refs]}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "HigherOrderGrounding":
        raw_refs = _mapping_sequence(payload, "evidence_pack_refs", required=True)
        return cls(tuple(EvidencePackReference.from_payload(item) for item in raw_refs))


@dataclass(frozen=True, slots=True)
class ResolvedEntityGrounding:
    """Resolver result for a pinned entity in a closure traversal."""

    direct_source_refs: tuple[SourceEvidenceRef, ...] = ()
    evidence_pack_refs: tuple[EvidencePackReference, ...] = ()


class EvidenceClosureResolver(Protocol):
    def resolve_pack(self, logical_ref: LogicalRef) -> EvidencePack | None: ...

    def resolve_entity(self, logical_ref: LogicalRef, *, revision_id: str) -> ResolvedEntityGrounding | None: ...

    def resolve_source(self, source_ref: SourceEvidenceRef) -> bool: ...


class EvidenceClosureValidator:
    """Validate typed evidence closure before an app-level authoritative write."""

    def __init__(self, resolver: EvidenceClosureResolver, *, max_refs: int = 256) -> None:
        if max_refs <= 0:
            raise ValueError("max_refs must be positive")
        self.resolver = resolver
        self.max_refs = max_refs

    def validate(
        self,
        grounding: HigherOrderGrounding,
        *,
        workspace_id: str,
        root_ref: LogicalRef,
        current_watermarks: Mapping[str, int] | None = None,
        allowed_namespaces: Sequence[str] | None = None,
    ) -> str:
        """Return the terminal source hash when the entire closure is valid."""

        namespaces = set(allowed_namespaces or (root_ref.target_namespace,))
        if not namespaces or root_ref.target_namespace not in namespaces:
            raise GroundingValidationError("root entity is outside the allowed graph namespaces")
        active_packs: set[tuple[str, str, str]] = set()
        completed_packs: set[tuple[str, str, str]] = set()
        active_entities: set[tuple[str, str, str, str]] = set()
        completed_entities: set[tuple[str, str, str, str]] = set()
        terminal_refs: list[SourceEvidenceRef] = []
        count = 0

        def check_source(source_ref: SourceEvidenceRef) -> None:
            nonlocal count
            count += 1
            if count > self.max_refs:
                raise GroundingValidationError("evidence closure exceeds configured reference bound")
            if source_ref.workspace_id != workspace_id:
                raise GroundingValidationError("evidence source crosses workspace boundary")
            if not self.resolver.resolve_source(source_ref):
                raise GroundingValidationError(
                    f"unresolvable source evidence {source_ref.source_unit_id!r}"
                )
            terminal_refs.append(source_ref)

        def check_entity(ref: PinnedEntityRef) -> None:
            nonlocal count
            count += 1
            if count > self.max_refs:
                raise GroundingValidationError("evidence closure exceeds configured reference bound")
            logical = ref.logical_ref
            if logical == root_ref:
                raise GroundingValidationError("evidence closure self-references the entity being written")
            key = (logical.target_namespace, logical.target_kind, logical.target_id, ref.revision_id)
            if key in active_entities:
                raise GroundingValidationError("cyclic evidence entity reference")
            if key in completed_entities:
                return
            active_entities.add(key)
            if logical.target_namespace not in namespaces:
                active_entities.remove(key)
                raise GroundingValidationError("evidence entity is outside the allowed namespace scope")
            try:
                if current_watermarks is not None and ref.event_seq > int(current_watermarks.get(logical.target_namespace, -1)):
                    raise GroundingValidationError("evidence entity references a future event watermark")
                resolved = self.resolver.resolve_entity(logical, revision_id=ref.revision_id)
                if resolved is None:
                    raise GroundingValidationError(f"unresolvable evidence entity {logical.target_id!r}")
                for source_ref in resolved.direct_source_refs:
                    check_source(source_ref)
                for pack_ref in resolved.evidence_pack_refs:
                    check_pack(pack_ref)
            finally:
                active_entities.remove(key)
            completed_entities.add(key)

        def check_pack(pack_ref: EvidencePackReference) -> None:
            nonlocal count
            count += 1
            if count > self.max_refs:
                raise GroundingValidationError("evidence closure exceeds configured reference bound")
            logical = pack_ref.pack_ref
            if logical == root_ref:
                raise GroundingValidationError("evidence closure self-references the entity being written")
            if logical.target_namespace not in namespaces:
                raise GroundingValidationError("evidence pack is outside the allowed namespace scope")
            key = (logical.target_namespace, logical.target_kind, logical.target_id)
            if key in active_packs:
                raise GroundingValidationError("cyclic evidence-pack reference")
            if key in completed_packs:
                return
            active_packs.add(key)
            pack = self.resolver.resolve_pack(logical)
            try:
                if pack is None:
                    raise GroundingValidationError(f"unresolvable evidence pack {logical.target_id!r}")
                if pack.namespace not in namespaces:
                    raise GroundingValidationError("evidence pack namespace is outside the allowed scope")
                if pack.content_hash != pack_ref.expected_hash:
                    raise GroundingValidationError(f"evidence pack hash mismatch for {pack.pack_id!r}")
                for namespace, watermark in pack.source_watermarks.items():
                    if current_watermarks is not None and int(watermark) > int(current_watermarks.get(namespace, -1)):
                        raise GroundingValidationError("evidence pack references a future source watermark")
                for namespace, watermark in pack_ref.source_watermark.items():
                    if current_watermarks is not None and int(watermark) > int(current_watermarks.get(namespace, -1)):
                        raise GroundingValidationError("evidence pack references a future source watermark")
                for source_ref in pack.source_refs:
                    check_source(source_ref)
                for entity_ref in pack.node_refs + pack.edge_refs:
                    check_entity(entity_ref)
            finally:
                active_packs.remove(key)
            completed_packs.add(key)

        for pack_ref in grounding.evidence_pack_refs:
            check_pack(pack_ref)
        if not terminal_refs:
            raise GroundingValidationError("evidence closure has no verified direct source terminal")
        terminal_payload = sorted(
            (ref.to_payload() for ref in terminal_refs),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
        return sha256(
            json.dumps(terminal_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()


__all__ = [
    "EvidenceClosureResolver",
    "EvidenceClosureValidator",
    "EvidencePack",
    "EvidencePackReference",
    "GroundingComposition",
    "GroundingValidationError",
    "HigherOrderGrounding",
    "PinnedEntityRef",
    "ResolvedEntityGrounding",
    "SourceEvidenceRef",
]
