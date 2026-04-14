from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest, NamespaceEngines, ProjectionEntity


class RecordingEngine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.writes: list[dict] = []


class TestPipeline(IngestPipeline):
    def __init__(self, engines: NamespaceEngines) -> None:
        super().__init__(engines)
        self.projected_entities: list[ProjectionEntity] = []

    def _write_source_document(self, namespace: str, request: IngestPipelineRequest) -> str:
        doc_id = f"doc:{request.workspace_id}:source"
        self.engines.conversation.writes.append({"kind": "document", "namespace": namespace, "id": doc_id, "title": request.title})
        return doc_id

    def _write_fragments(self, namespace: str, source_document_id: str, request: IngestPipelineRequest) -> None:
        self.engines.conversation.writes.append({"kind": "fragment", "namespace": namespace, "source_document_id": source_document_id})

    def _write_maintenance_job(self, namespace: str, request: IngestPipelineRequest, source_document_id: str) -> str:
        job_id = f"job:{request.workspace_id}:maintenance"
        self.engines.workflow.writes.append({"kind": "maintenance_job_request", "namespace": namespace, "id": job_id, "source_document_id": source_document_id})
        return job_id

    def _write_candidate_link(self, namespace: str, request: IngestPipelineRequest, source_document_id: str) -> str:
        candidate_id = f"candidate:{request.workspace_id}:crosslink"
        self.engines.conversation.writes.append({"kind": "candidate_link", "namespace": namespace, "id": candidate_id, "source_document_id": source_document_id})
        return candidate_id

    def _write_promotion_candidate(self, review_namespace: str, request: IngestPipelineRequest, candidate_link_id: str) -> str:
        promotion_id = f"promotion:{request.workspace_id}:candidate"
        self.engines.conversation.writes.append({"kind": "promotion_candidate", "namespace": review_namespace, "id": promotion_id, "candidate_link_id": candidate_link_id})
        return promotion_id

    def _promote_candidate(self, namespace: str, request: IngestPipelineRequest, promotion_candidate_id: str) -> str:
        edge_id = f"edge:{request.workspace_id}:promoted"
        self.engines.kg.writes.append({"kind": "edge", "namespace": namespace, "id": edge_id, "promotion_candidate_id": promotion_candidate_id, "relation": "related_to"})
        self.projected_entities = [ProjectionEntity(title="Payment Terms", relationships=["related_to:Acme Contract"])]
        return edge_id

    def _read_projection_entities(self):
        return list(self.projected_entities)


@pytest.fixture()
def namespace_engines():
    conversation = RecordingEngine("conversation")
    return NamespaceEngines(
        conversation=conversation,
        workflow=RecordingEngine("workflow"),
        kg=RecordingEngine("kg"),
        wisdom=RecordingEngine("wisdom"),
    )


@pytest.fixture()
def pipeline(namespace_engines):
    return TestPipeline(namespace_engines)


@pytest.fixture()
def ingest_request():
    return IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days. Either party may terminate with notice.",
    )


@pytest.fixture()
def seeded_kg_node(pipeline):
    pipeline.projected_entities = [ProjectionEntity(title="Payment Terms", relationships=[])]
    return pipeline.projected_entities[0]
