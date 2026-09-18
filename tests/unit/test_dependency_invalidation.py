from types import SimpleNamespace

import pytest
from kogwistar.engine_core.models import Grounding, Node, Span

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.maintenance.dependency_planning import (
    plan_dependency_invalidation,
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
from kogwistar_llm_wiki.worker import MaintenanceWorker


def test_invalidation_uses_existing_edge_endpoints_and_same_workspace_only() -> None:
    plan = plan_dependency_invalidation(
        workspace_id="workspace-a",
        changed_entity_ids={"node:changed"},
        nodes=[
            SimpleNamespace(
                id="summary:a",
                metadata={
                    "workspace_id": "workspace-a",
                    "artifact_kind": "derived_summary",
                    "source_document_id": "source:a",
                    "lineage_node_ids": ["node:changed"],
                },
            ),
            SimpleNamespace(
                id="summary:foreign",
                metadata={
                    "workspace_id": "workspace-b",
                    "artifact_kind": "derived_summary",
                    "source_document_id": "source:b",
                    "lineage_node_ids": ["node:changed"],
                },
            ),
        ],
        edges=[
            SimpleNamespace(
                id="edge:changed",
                source_ids=["node:changed"],
                target_ids=["node:other"],
                metadata={
                    "workspace_id": "workspace-a",
                    "artifact_kind": "crosslink",
                    "source_document_id": "source:a",
                },
            )
        ],
    )

    assert plan.affected_entity_ids == ("edge:changed", "summary:a")
    assert plan.affected_source_document_ids == ("source:a",)
    assert plan.follow_up_kinds == ("document_propose_crosslinks", "document_summarize_units")
    assert plan.skipped_cross_workspace_ids == ("summary:foreign",)


def test_invalidation_is_bounded_and_deterministic() -> None:
    nodes = [
        SimpleNamespace(
            id=f"summary:{index}",
            metadata={
                "workspace_id": "w",
                "artifact_kind": "summary",
                "source_document_id": f"source:{index}",
                "depends_on_ids": ["changed"],
            },
        )
        for index in range(3)
    ]
    plan = plan_dependency_invalidation(
        workspace_id="w",
        changed_entity_ids=["changed"],
        nodes=nodes,
        edges=(),
        max_dependents=2,
    )

    assert plan.affected_entity_ids == ("summary:0", "summary:1")
    assert plan.truncated is True


def test_invalidation_rejects_non_positive_bound() -> None:
    with pytest.raises(ValueError, match="max_dependents"):
        plan_dependency_invalidation(
            workspace_id="w",
            changed_entity_ids=["changed"],
            nodes=(),
            edges=(),
            max_dependents=0,
        )


def test_worker_enqueues_one_bounded_same_workspace_invalidation_job(pipeline, ingest_request) -> None:
    request = ingest_request.model_copy(update={"workspace_id": "invalidation-w"})
    source_id = pipeline._source_document_id(request)
    ns = WorkspaceNamespaces(request.workspace_id)
    pipeline.register_source(request=request, source_document_id=source_id, namespace=ns.conv_bg)
    with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
        pipeline.engines.kg.write.add_node(
            Node(
                id="summary:1",
                label="Derived summary",
                type="entity",
                summary="A derived summary",
                mentions=[Grounding(spans=[Span.from_dummy_for_conversation("summary:1")])],
                metadata={
                    "workspace_id": request.workspace_id,
                    "graph_space": "curated_kg",
                    "artifact_kind": "derived_summary",
                    "source_document_id": source_id,
                    "lineage_node_ids": ["new:node"],
                },
            )
        )
    patch = MaintenancePatch(
        patch_id="patch-invalidation",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=MaintenanceScope(workspace_id=request.workspace_id),
        operations=[
            MaintenancePatchOperation(
                operation_id="add-new-node",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="new:node",
                provenance=MaintenanceProvenance(
                    source_document_id=source_id,
                    source_pointers=[{"doc_id": source_id, "start_char": 0, "end_char": 1}],
                    maintenance_run_id="run-invalidation",
                    confidence=0.9,
                ),
            )
        ],
    )
    worker = MaintenanceWorker(pipeline.engines)
    context = SimpleNamespace(
        workspace_id=request.workspace_id,
        job_id="patch-job",
        payload={},
    )

    plan = worker._plan_and_enqueue_dependency_invalidation(context, patch)

    assert plan is not None
    jobs = pipeline.engines.conversation.jobs.list(namespace=ns.maintenance_jobs, status="PENDING", limit=10)
    assert len(jobs) == 1
    assert jobs[0].payload["maintenance_origin"] == "dependency_invalidation"
    assert jobs[0].payload["maintenance_max_rounds"] == 1


def test_worker_does_not_start_a_second_invalidation_wave(pipeline, ingest_request) -> None:
    worker = MaintenanceWorker(pipeline.engines)
    context = SimpleNamespace(
        workspace_id=ingest_request.workspace_id,
        job_id="invalidation-job",
        payload={"maintenance_origin": "dependency_invalidation"},
    )
    patch = SimpleNamespace(operations=[])

    assert worker._plan_and_enqueue_dependency_invalidation(context, patch) is None
