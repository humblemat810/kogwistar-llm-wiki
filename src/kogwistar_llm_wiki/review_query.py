from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from kogwistar.engine_core.models import Node

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace


@dataclass(frozen=True, slots=True)
class ReviewChainResult:
    promoted_node: Any
    candidate_link: Any | None = None
    promotion_candidate: Any | None = None
    promotion_evidence_pack: Any | None = None


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
        query_where = dict(where or {})
        if candidate_link_id is not None:
            query_where["candidate_link_id"] = candidate_link_id
        return self.get_review_nodes(
            workspace_id=workspace_id,
            artifact_kinds=["promotion_evidence_pack"],
            where=query_where,
        )

    def get_review_chain_for_promoted_node(
        self,
        *,
        workspace_id: str,
        promoted_node_id: str,
    ) -> ReviewChainResult | None:
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


__all__ = ["ReviewChainResult", "ReviewQueryService"]
