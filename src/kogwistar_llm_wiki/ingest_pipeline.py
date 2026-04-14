from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import IngestPipelineArtifacts, IngestPipelineRequest
from .namespaces import WorkspaceNamespaces


@dataclass(slots=True)
class NamespaceEngines:
    conversation: Any
    workflow: Any
    kg: Any
    wisdom: Any


@dataclass(slots=True)
class ProjectionEntity:
    title: str
    relationships: list[str]


@dataclass(slots=True)
class ProjectionSnapshot:
    entities: list[ProjectionEntity]


def build_in_memory_namespace_engines() -> NamespaceEngines:
    conversation = object()
    return NamespaceEngines(
        conversation=conversation,
        workflow=object(),
        kg=object(),
        wisdom=object(),
    )


class IngestPipeline:
    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def namespaces_for(self, workspace_id: str) -> WorkspaceNamespaces:
        return WorkspaceNamespaces(workspace_id)

    def run(self, request: IngestPipelineRequest) -> IngestPipelineArtifacts:
        ns = self.namespaces_for(request.workspace_id)
        source_document_id = self._write_source_document(ns.conv_fg, request)
        self._write_fragments(ns.conv_fg, source_document_id, request)
        maintenance_job_id = self._write_maintenance_job(ns.workflow_maintenance, request, source_document_id)
        candidate_link_id = self._write_candidate_link(ns.conv_bg, request, source_document_id)
        promotion_candidate_id = self._write_promotion_candidate(ns.review, request, candidate_link_id)

        promoted_edge_id: str | None = None
        if request.auto_accept_threshold >= 0.95:
            promoted_edge_id = self._promote_candidate(ns.kg, request, promotion_candidate_id)

        return IngestPipelineArtifacts(
            source_document_id=source_document_id,
            maintenance_job_id=maintenance_job_id,
            candidate_link_id=candidate_link_id,
            promotion_candidate_id=promotion_candidate_id,
            promoted_edge_id=promoted_edge_id,
        )

    def build_projection_snapshot(self, workspace_id: str | None = None) -> ProjectionSnapshot:
        return ProjectionSnapshot(entities=self._read_projection_entities())

    def _write_source_document(self, namespace: str, request: IngestPipelineRequest) -> str:
        return f"doc:{request.workspace_id}:{request.title}"

    def _write_fragments(self, namespace: str, source_document_id: str, request: IngestPipelineRequest) -> None:
        return None

    def _write_maintenance_job(self, namespace: str, request: IngestPipelineRequest, source_document_id: str) -> str:
        return f"job:{request.workspace_id}:{source_document_id}"

    def _write_candidate_link(self, namespace: str, request: IngestPipelineRequest, source_document_id: str) -> str:
        return f"candidate:{request.workspace_id}:{source_document_id}"

    def _write_promotion_candidate(self, review_namespace: str, request: IngestPipelineRequest, candidate_link_id: str) -> str:
        return f"promotion:{request.workspace_id}:{candidate_link_id}"

    def _promote_candidate(self, namespace: str, request: IngestPipelineRequest, promotion_candidate_id: str) -> str:
        return f"edge:{request.workspace_id}:{promotion_candidate_id}"

    def _read_projection_entities(self) -> list[ProjectionEntity]:
        return []
