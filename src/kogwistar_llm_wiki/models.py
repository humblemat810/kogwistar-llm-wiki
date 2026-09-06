from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal

from pydantic import BaseModel
from pydantic_extension.model_slicing import ModeSlicingMixin, DtoType

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar_obsidian_sink.core.models import ProjectionEntity


@dataclass(frozen=True, slots=True)
class IngestPipelineArtifacts:
    source_document_id: str
    maintenance_job_id: str
    candidate_link_id: str
    promotion_candidate_id: str
    promoted_entity_id: str | None
    operation_mode: str = "parse_first"
    graph_status: str = "expanding"


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
    outputs: Dict[str, object]
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
    operation_mode: DtoType[str] = "parse_first"
    parser_mode: DtoType[str] = "heuristic"
    parser_lane: DtoType[str] = "page_index"
    promotion_mode: DtoType[str] = "pending"
    auto_accept_threshold: DtoType[float] = 0.95
    llm_provider: DtoType[str | None] = None
    llm_model: DtoType[str | None] = None
    provenance_policy: DtoType[Literal["required", "optional", "disabled"]] = "optional"
    provenance: DtoType[dict[str, object] | None] = None


@dataclass(slots=True)
class NamespaceEngines:
    conversation: GraphKnowledgeEngine  # Shared by conv:fg and conv:bg
    workflow: GraphKnowledgeEngine      # For wf:maintenance
    kg: GraphKnowledgeEngine            # Knowledge-family engine
    wisdom: GraphKnowledgeEngine        # For wisdom
    derived_knowledge: GraphKnowledgeEngine | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    def derived_knowledge_engine(self) -> GraphKnowledgeEngine:
        return self.derived_knowledge or self.kg

    def close(self) -> None:
        """Close each owned engine exactly once.

        ``derived_knowledge`` may alias ``kg``; identity de-duplication keeps
        cleanup safe for both split and shared graph layouts.
        """
        if self._closed:
            return
        seen: set[int] = set()
        first_error: Exception | None = None
        for engine in (
            self.conversation,
            self.workflow,
            self.kg,
            self.wisdom,
            self.derived_knowledge,
        ):
            if engine is None or id(engine) in seen:
                continue
            seen.add(id(engine))
            close = getattr(engine, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    if first_error is None:
                        first_error = exc
        self._closed = True
        if first_error is not None:
            raise first_error


@dataclass(slots=True)
class ProjectionSnapshot:
    entities: list[ProjectionEntity]
