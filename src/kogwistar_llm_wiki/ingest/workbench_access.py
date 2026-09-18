"""Workbench and investigation accessors for the ingestion pipeline."""

from __future__ import annotations

from collections.abc import Mapping

from ..configuration.workspace import GraphSpace
from ..models import ProjectionSnapshot
from ..workbench.investigation_history import InvestigationHistoryRecord
from ..workbench.query import GraphSpaceQueryResult
from ..workbench.semantic_lens import (
    InvestigationOutcome,
    SemanticLensRequest,
    SemanticLensSnapshot,
)


class WorkbenchAccessMixin:
    """Expose bounded, app-owned workbench services through ``IngestPipeline``."""

    def build_projection_snapshot(
        self,
        workspace_id: str,
        *,
        graph_spaces: list[GraphSpace | str] | None = None,
        projection_filter: str | None = None,
    ) -> ProjectionSnapshot:
        return self.projection.build_projection_snapshot(
            workspace_id,
            graph_spaces=graph_spaces,
            projection_filter=projection_filter,
        )

    def query_nodes(
        self,
        *,
        workspace_id: str,
        graph_spaces: list[GraphSpace | str],
        where: Mapping[str, object] | None = None,
        resolve_mode: str = "pointer_only",
    ) -> list[GraphSpaceQueryResult]:
        return self.query_service.get_nodes(
            workspace_id=workspace_id,
            graph_spaces=graph_spaces,
            where=where,
            resolve_mode=resolve_mode,
        )

    def resolve_semantic_lens(self, request: SemanticLensRequest) -> SemanticLensSnapshot:
        """Resolve a bounded workbench lens through the app-owned service."""
        return self.semantic_lens_service.resolve(request)

    def record_investigation(
        self,
        *,
        workspace_id: str,
        session_id: str,
        question: str,
        action_kind: str,
        snapshot: SemanticLensSnapshot,
        outcome: InvestigationOutcome,
        created_at_ms: int | None = None,
    ) -> InvestigationHistoryRecord:
        from ..debug_run import now_ms

        return self.investigation_history_service.record(
            workspace_id=workspace_id,
            session_id=session_id,
            question=question,
            action_kind=action_kind,
            snapshot=snapshot,
            outcome=outcome,
            created_at_ms=now_ms() if created_at_ms is None else created_at_ms,
        )

    def query_investigation_history(
        self,
        *,
        workspace_id: str,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[InvestigationHistoryRecord]:
        return self.investigation_history_service.query(
            workspace_id=workspace_id,
            session_id=session_id,
            limit=limit,
        )
