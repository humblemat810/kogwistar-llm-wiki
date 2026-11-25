from __future__ import annotations

import os
import uuid

import pytest
from kogwistar.engine_core.models import Grounding, Node, Span
from sqlalchemy import create_engine, text

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_postgres_namespace_engines
from kogwistar_llm_wiki.maintenance.maintenance_patch_apply import (
    apply_maintenance_patch,
)
from kogwistar_llm_wiki.maintenance.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from kogwistar_llm_wiki.utils import _temporary_namespace

pytestmark = [pytest.mark.integration, pytest.mark.ci_full, pytest.mark.slow]


def test_postgres_acl_enabled_crosslink_preserves_matching_span_policies(
    tmp_path,
) -> None:
    dsn = os.getenv("LLM_WIKI_TEST_PG_DSN")
    if not dsn:
        pytest.skip("set LLM_WIKI_TEST_PG_DSN to run the live pgvector ACL profile")
    pytest.importorskip("pgvector")

    workspace_id = f"acl-pg-profile-{uuid.uuid4().hex[:8]}"
    schema = f"acl_profile_{uuid.uuid4().hex[:16]}"
    admin = create_engine(dsn, pool_pre_ping=True)
    engines = None
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        engines = build_postgres_namespace_engines(
            base_dir=tmp_path / "engines",
            dsn=dsn,
            schema=schema,
            embedding_function=lambda values: [[1.0, 0.0] for _ in values],
            embedding_provider="fake",
            embedding_model="acl-pgvector-profile",
            embedding_dimension=2,
            acl_enabled=True,
        )
        engine = engines.kg
        assert engine.acl_enabled is True

        namespace = WorkspaceNamespaces(workspace_id).curated_kg_space
        endpoint_ids = (
            f"ws:{workspace_id}:node:left",
            f"ws:{workspace_id}:node:right",
        )
        source_values = (("doc-left", "alpha source"), ("doc-right", "omega source"))
        with _temporary_namespace(engine, namespace):
            for node_id, (doc_id, excerpt) in zip(endpoint_ids, source_values, strict=True):
                engine.write.add_node(
                    Node(
                        id=node_id,
                        label=node_id.rsplit(":", 1)[-1],
                        type="entity",
                        summary=excerpt,
                        doc_id=doc_id,
                        mentions=[
                            Grounding(
                                spans=[
                                    Span(
                                        doc_id=doc_id,
                                        start_char=0,
                                        end_char=len(excerpt),
                                        excerpt=excerpt,
                                        document_page_url="",
                                        collection_page_url="",
                                        insertion_method="postgres-acl-profile",
                                    )
                                ]
                            )
                        ],
                        metadata={"workspace_id": workspace_id, "acl_mode": "public"},
                    )
                )

            edge_id = f"ws:{workspace_id}:edge:crosslink"
            patch = MaintenancePatch(
                patch_id=f"patch-{uuid.uuid4().hex}",
                intent=MaintenanceIntent.ADD_CROSSLINK,
                scope=MaintenanceScope(workspace_id=workspace_id),
                operations=[
                    MaintenancePatchOperation(
                        operation_id="apply-crosslink",
                        kind=MaintenanceOperationKind.ADD_EDGE,
                        edge_id=edge_id,
                        from_node_id=endpoint_ids[0],
                        to_node_id=endpoint_ids[1],
                        relation="supports",
                        properties={"crosslink_status": "accepted"},
                        provenance=MaintenanceProvenance(
                            source_document_id="doc-left",
                            source_pointers=[
                                {
                                    "doc_id": doc_id,
                                    "start_char": 0,
                                    "end_char": len(excerpt),
                                    "excerpt": excerpt,
                                }
                                for doc_id, excerpt in source_values
                            ],
                            maintenance_run_id="postgres-acl-profile",
                            confidence=0.9,
                        ),
                    )
                ],
            )
            result = apply_maintenance_patch(
                engine,
                patch,
                namespace_prefix=f"ws:{workspace_id}:",
            )
            assert result.status.value == "applied"
            persisted_edges = engine.read.get_edges(ids=[edge_id])
            assert len(persisted_edges) == 1
            edge = persisted_edges[0]
            assert [(span.doc_id, span.excerpt) for span in edge.iter_span()] == [
                source_values[0],
                source_values[1],
            ]

        def policy(grain: str, entity_id: str, target_item_id: str | None = None):
            return engine.acl_graph.latest_record(
                grain=grain,
                truth_graph=engine.kg_graph_type,
                entity_id=entity_id,
                target_item_id=target_item_id,
            )

        def signature(record):
            assert record is not None
            return (
                record.mode,
                record.owner_id,
                record.security_scope,
                record.shared_with_principals,
                record.shared_with_groups,
            )

        expected = signature(policy("node", endpoint_ids[0]))
        assert expected[0] == "public"
        assert signature(policy("node", endpoint_ids[1])) == expected
        assert signature(policy("edge", edge_id)) == expected
        grounding_id = f"{edge_id}::mention::0"
        assert signature(policy("grounding", edge_id, grounding_id)) == expected
        for span_index in range(2):
            span_id = f"{grounding_id}::span::{span_index}"
            assert signature(policy("span", edge_id, span_id)) == expected
    finally:
        if engines is not None:
            engines.close()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
