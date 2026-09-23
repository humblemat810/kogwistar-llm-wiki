"""Authorization-aware dereferencing for embedding evidence references."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from kogwistar.engine_core import EmbeddingReference, PinnedLogicalRef


DereferenceStatus = Literal["available", "stale", "unauthorized", "unresolved"]


class EmbeddingReferenceResolver(Protocol):
    """Application-owned resolver; implementations must enforce ACLs."""

    def resolve_source_map(self, reference: PinnedLogicalRef) -> bool: ...

    def authorize_target(self, reference: PinnedLogicalRef) -> bool: ...


@dataclass(frozen=True, slots=True)
class EmbeddingDereferenceResult:
    status: DereferenceStatus
    source_map: PinnedLogicalRef
    authorized_targets: tuple[PinnedLogicalRef, ...] = ()


class EmbeddingReferenceDereferencer:
    """Resolve a reference without promoting vector similarity to graph truth."""

    def __init__(self, resolver: EmbeddingReferenceResolver) -> None:
        self.resolver = resolver

    def resolve(
        self,
        reference: EmbeddingReference,
        *,
        workspace_id: str,
        allowed_namespaces: Sequence[str] | None = None,
    ) -> EmbeddingDereferenceResult:
        allowed = set(allowed_namespaces or (workspace_id,))
        if workspace_id not in allowed or reference.source_namespace not in allowed:
            return EmbeddingDereferenceResult(
                status="unauthorized",
                source_map=next(
                    target for target in reference.targets if target.role == "source_map"
                ),
            )

        source_map = next(
            target for target in reference.targets if target.role == "source_map"
        )
        if not self.resolver.resolve_source_map(source_map):
            return EmbeddingDereferenceResult(status="stale", source_map=source_map)

        authorized: list[PinnedLogicalRef] = []
        for target in reference.targets:
            if target is source_map:
                continue
            if target.logical_ref.target_namespace not in allowed:
                return EmbeddingDereferenceResult(
                    status="unauthorized",
                    source_map=source_map,
                    authorized_targets=tuple(authorized),
                )
            if not self.resolver.authorize_target(target):
                return EmbeddingDereferenceResult(
                    status="unauthorized",
                    source_map=source_map,
                    authorized_targets=tuple(authorized),
                )
            authorized.append(target)
        return EmbeddingDereferenceResult(
            status="available",
            source_map=source_map,
            authorized_targets=tuple(authorized),
        )


__all__ = [
    "DereferenceStatus",
    "EmbeddingDereferenceResult",
    "EmbeddingReferenceDereferencer",
    "EmbeddingReferenceResolver",
]
