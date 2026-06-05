"""Helpers for querying review-shaped artifacts from background conversation.

This module keeps Phase 6 intentionally small: review remains stored in the
conversation background lane, and the app exposes a thin, explicit query
surface for the existing review artifact chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from kogwistar.engine_core.models import Node

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace


@dataclass(frozen=True, slots=True)
class ReviewChainResult:
    """Resolved review chain for a promoted node."""

    promoted_node: Any
    candidate_link: Any | None = None
    promotion_candidate: Any | None = None
    promotion_evidence_pack: Any | None = None


@dataclass(frozen=True, slots=True)
class MaintenancePatchReport:
    """Aggregated status counts for maintenance patch artifacts."""

    patch_count: int
    proposed_operations: int = 0
    applied_operations: int = 0
    rejected_operations: int = 0
    retracted_operations: int = 0
    skipped_operations: int = 0
    failed_operations: int = 0


class ReviewQueryService:
    """Tiny app-level helper for review artifacts stored in background conversation."""

    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def get_review_nodes(
        self,
        *,
        workspace_id: str,
        artifact_kinds: Sequence[str] | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> list[Node]:
        """Return review artifacts from the background conversation namespace."""
        ns = WorkspaceNamespaces(workspace_id)
        query_where = dict(where or {})
        query_where["workspace_id"] = workspace_id
        query_where.setdefault("conversation_lane", "background")

        if artifact_kinds is None:
            kinds = {"candidate_link", "promotion_candidate", "promotion_evidence_pack"}
        else:
            kinds = {str(kind).strip() for kind in artifact_kinds if str(kind).strip()}
        if not kinds:
            return []

        results: list[Node] = []
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            for kind in sorted(kinds):
                nodes = self.engines.conversation.read.get_nodes(
                    where={**query_where, "artifact_kind": kind},
                    limit=10_000,
                )
                results.extend(nodes)
        return results

    def get_candidate_links(
        self,
        *,
        workspace_id: str,
        where: Mapping[str, Any] | None = None,
    ) -> list[Node]:
        """Return candidate-link artifacts for a workspace."""
        return self.get_review_nodes(
            workspace_id=workspace_id,
            artifact_kinds=["candidate_link"],
            where=where,
        )

    def get_promotion_candidates(
        self,
        *,
        workspace_id: str,
        candidate_link_id: str | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> list[Node]:
        """Return promotion-candidate artifacts, optionally scoped to a link."""
        query_where = dict(where or {})
        if candidate_link_id is not None:
            query_where["candidate_link_id"] = candidate_link_id
        return self.get_review_nodes(
            workspace_id=workspace_id,
            artifact_kinds=["promotion_candidate"],
            where=query_where,
        )

    def get_promotion_evidence_packs(
        self,
        *,
        workspace_id: str,
        candidate_link_id: str | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> list[Node]:
        """Return promotion-evidence-pack artifacts, optionally scoped to a link."""
        query_where = dict(where or {})
        if candidate_link_id is not None:
            query_where["candidate_link_id"] = candidate_link_id
        return self.get_review_nodes(
            workspace_id=workspace_id,
            artifact_kinds=["promotion_evidence_pack"],
            where=query_where,
        )

    def get_maintenance_patch_artifacts(
        self,
        *,
        workspace_id: str,
        patch_id: str | None = None,
        status: str | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> list[Node]:
        """Return patch-applied/failed artifacts from the curated KG namespace."""
        ns = WorkspaceNamespaces(workspace_id)
        query_where = dict(where or {})
        query_where["workspace_id"] = workspace_id
        query_where["artifact_kind"] = "maintenance_patch_artifact"
        if patch_id is not None:
            query_where["patch_id"] = patch_id
        if status is not None:
            query_where["patch_status"] = status

        with _temporary_namespace(self.engines.kg, ns.curated_kg_space):
            return self.engines.kg.read.get_nodes(
                where=query_where,
                limit=10_000,
                resolve_mode="include_tombstones",
            )

    def get_maintenance_patch_report(
        self,
        *,
        workspace_id: str,
        where: Mapping[str, Any] | None = None,
    ) -> MaintenancePatchReport:
        """Aggregate patch artifact metadata into operator-facing counts."""
        artifacts = self.get_maintenance_patch_artifacts(
            workspace_id=workspace_id,
            where=where,
        )
        proposed = applied = rejected = retracted = skipped = failed = 0
        for artifact in artifacts:
            metadata = dict(getattr(artifact, "metadata", None) or {})
            status = str(metadata.get("patch_status") or "")
            operation_count = _int_metadata(metadata.get("operation_count"))
            if status in {"proposed", "validated", "partially_accepted"}:
                proposed += operation_count
            elif status == "applied":
                applied += _int_metadata(metadata.get("applied_count"), operation_count)
            elif status in {"rejected", "needs_review"}:
                rejected += operation_count
            elif status == "retracted":
                retracted += operation_count
            skipped += _int_metadata(metadata.get("skipped_count"))
            failed += _int_metadata(metadata.get("failed_count"))
        return MaintenancePatchReport(
            patch_count=len(artifacts),
            proposed_operations=proposed,
            applied_operations=applied,
            rejected_operations=rejected,
            retracted_operations=retracted,
            skipped_operations=skipped,
            failed_operations=failed,
        )

    def get_review_chain_for_promoted_node(
        self,
        *,
        workspace_id: str,
        promoted_node_id: str,
    ) -> ReviewChainResult | None:
        """Resolve the review chain that led to a promoted curated node."""
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.kg, ns.curated_kg_space):
            promoted_nodes = self.engines.kg.read.get_nodes(
                ids=[promoted_node_id],
                limit=1,
            )
        if not promoted_nodes:
            return None
        promoted = promoted_nodes[0]
        promoted_md = dict(getattr(promoted, "metadata", None) or {})
        promotion_candidate_id = str(promoted_md.get("promotion_candidate_id") or "").strip()
        promotion_evidence_pack_id = str(promoted_md.get("promotion_evidence_pack_id") or "").strip()
        if not promotion_candidate_id or not promotion_evidence_pack_id:
            return ReviewChainResult(promoted_node=promoted)

        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            candidate_nodes = self.engines.conversation.read.get_nodes(
                ids=[promotion_candidate_id],
                limit=1,
            )
            evidence_nodes = self.engines.conversation.read.get_nodes(
                ids=[promotion_evidence_pack_id],
                limit=1,
            )
        promotion_candidate = candidate_nodes[0] if candidate_nodes else None
        promotion_evidence_pack = evidence_nodes[0] if evidence_nodes else None

        candidate_link = None
        candidate_link_id = ""
        if promotion_candidate is not None:
            candidate_md = dict(getattr(promotion_candidate, "metadata", None) or {})
            candidate_link_id = str(candidate_md.get("candidate_link_id") or "").strip()
        if not candidate_link_id and promotion_evidence_pack is not None:
            pack_md = dict(getattr(promotion_evidence_pack, "metadata", None) or {})
            candidate_link_id = str(pack_md.get("candidate_link_id") or "").strip()
        if candidate_link_id:
            with _temporary_namespace(self.engines.conversation, ns.conv_bg):
                candidate_link_nodes = self.engines.conversation.read.get_nodes(
                    ids=[candidate_link_id],
                    limit=1,
                )
            candidate_link = candidate_link_nodes[0] if candidate_link_nodes else None

        return ReviewChainResult(
            promoted_node=promoted,
            candidate_link=candidate_link,
            promotion_candidate=promotion_candidate,
            promotion_evidence_pack=promotion_evidence_pack,
        )


def _int_metadata(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["MaintenancePatchReport", "ReviewChainResult", "ReviewQueryService"]
