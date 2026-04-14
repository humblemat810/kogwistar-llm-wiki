from __future__ import annotations

import pytest
from kogwistar.engine_core.models import Grounding, Node, Span

from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest, build_in_memory_namespace_engines


@pytest.fixture()
def workspace_id() -> str:
    return "demo"


@pytest.fixture()
def pipeline(tmp_path, workspace_id: str) -> IngestPipeline:
    engines = build_in_memory_namespace_engines(workspace_id=workspace_id, persist_root=str(tmp_path))
    return IngestPipeline(engines=engines)


@pytest.fixture()
def ingest_request(workspace_id: str) -> IngestPipelineRequest:
    return IngestPipelineRequest(
        workspace_id=workspace_id,
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text=(
            "Acme Contract\n\n"
            "Payment Terms\n"
            "Invoices are due within 30 days.\n\n"
            "Termination\n"
            "Either party may terminate with notice."
        ),
    )


@pytest.fixture()
def seeded_kg_node(pipeline: IngestPipeline, workspace_id: str) -> Node:
    node = Node(
        label="Payment Terms",
        type="entity",
        summary="Existing promoted knowledge node",
        mentions=[
            Grounding(
                spans=[
                    Span(
                        collection_page_url="collection/seed",
                        document_page_url="document/seed/page/1",
                        doc_id="seed-doc",
                        insertion_method="system",
                        page_number=1,
                        start_char=0,
                        end_char=13,
                        excerpt="Payment Terms",
                        context_before="",
                        context_after="",
                    )
                ]
            )
        ],
        doc_id="seed-doc",
        metadata={
            "namespace": f"ws:{workspace_id}:kg",
            "artifact_type": "seed_entity",
            "visibility": "user",
        },
        properties={"seeded": True},
    )
    pipeline.engines.kg.add_node(node, doc_id="seed-doc")
    return node
