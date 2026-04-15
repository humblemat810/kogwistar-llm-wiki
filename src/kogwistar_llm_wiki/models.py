from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel
from pydantic_extension.model_slicing import ModeSlicingMixin, DtoType, BackendType

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar_obsidian_sink.core.models import ProjectionEntity


@dataclass(frozen=True, slots=True)
class IngestPipelineArtifacts:
    source_document_id: str
    maintenance_job_id: str
    candidate_link_id: str
    promotion_candidate_id: str
    promoted_entity_id: str | None


@dataclass(frozen=True, slots=True)
class MaintenanceJobRequest:
    job_type: str
    workspace_id: str
    trigger_type: str
    candidate_ids: list[str]
    requested_by: str = "system"
    priority: int = 10
    policy_version: str = "1.0"


@dataclass(frozen=True, slots=True)
class MaintenanceJobResult:
    job_id: str
    job_type: str
    outputs: Dict[str, Any]
    review_required: bool
    emitted_event_ids: list[str]
    status: str = "completed"


@dataclass(frozen=True, slots=True)
class ObsidianBuildResult:
    vault_root: Path
    notes: int
    canvases: int
    dangling_links: int


class IngestPipelineRequest(ModeSlicingMixin, BaseModel):
    workspace_id: DtoType[str]
    source_uri: DtoType[str]
    title: DtoType[str]
    raw_text: DtoType[str]
    source_format: DtoType[str] = "text"
    parser_mode: DtoType[str] = "heuristic"
    promotion_mode: DtoType[str] = "pending"
    auto_accept_threshold: DtoType[float] = 0.95
    llm_provider: DtoType[str | None] = None
    llm_model: DtoType[str | None] = None


@dataclass(slots=True)
class NamespaceEngines:
    conversation: GraphKnowledgeEngine  # Shared by conv:fg and conv:bg
    workflow: GraphKnowledgeEngine      # For wf:maintenance
    kg: GraphKnowledgeEngine            # For kg
    wisdom: GraphKnowledgeEngine        # For wisdom


@dataclass(slots=True)
class ProjectionSnapshot:
    entities: list[ProjectionEntity]
