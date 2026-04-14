from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class IngestPipelineRequest:
    workspace_id: str
    source_uri: str
    title: str
    raw_text: str
    source_format: str = "text"
    parser_mode: str = "heuristic"
    auto_accept_threshold: float = 0.95


@dataclass(frozen=True, slots=True)
class IngestPipelineArtifacts:
    source_document_id: str
    maintenance_job_id: str
    candidate_link_id: str
    promotion_candidate_id: str
    promoted_entity_id: str | None


@dataclass(frozen=True, slots=True)
class ObsidianBuildResult:
    vault_root: Path
    notes: int
    canvases: int
    dangling_links: int
