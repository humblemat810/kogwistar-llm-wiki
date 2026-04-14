from __future__ import annotations

from dataclasses import dataclass


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
    source_node_id: str
    maintenance_request_id: str
    candidate_link_node_ids: tuple[str, ...]
    promotion_candidate_node_ids: tuple[str, ...]
    promoted_edge_ids: tuple[str, ...]
