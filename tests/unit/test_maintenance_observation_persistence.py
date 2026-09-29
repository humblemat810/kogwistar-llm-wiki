from __future__ import annotations

from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.configuration.identity import durable_claims_context
from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.maintenance.maintenance_observation import (
    ObservationSubject,
    assess_observation_frame,
    build_observation_frame,
)
from kogwistar_llm_wiki.maintenance.worker_observation import (
    MaintenanceObservationWorkerMixin,
)


@pytest.mark.slow
def test_observation_assessment_persists_once_on_duplicate_delivery(tmp_path) -> None:
    workspace_id = "observation-audit-test"
    namespaces = WorkspaceNamespaces(workspace_id)
    engines = build_in_memory_namespace_engines(tmp_path)
    worker = object.__new__(MaintenanceObservationWorkerMixin)
    worker.engines = engines
    subject = ObservationSubject(
        kind="node",
        subject_id="node:reviewed",
        workspace_id=workspace_id,
        namespace=namespaces.curated_kg_space,
    )
    frame = build_observation_frame(subject)
    assessment = assess_observation_frame(frame)
    ctx = SimpleNamespace(
        workspace_id=workspace_id,
        job_id="maintenance-job:observation-audit",
        request_node_id="maintenance-request:observation-audit",
    )

    try:
        with durable_claims_context({"storage_ns": namespaces.conv_bg}):
            worker._persist_observation_audit(ctx, frame, assessment)
            worker._persist_observation_audit(ctx, frame, assessment)

        with worker._observation_namespace(namespaces.conv_bg):
            messages = engines.conversation.read.get_nodes(
                where={"kind": "lane_message"},
                limit=100,
            )
        audit_messages = [
            node
            for node in messages
            if node.metadata.get("msg_type") == "maintenance.observation.assessment"
            and node.metadata.get("idempotency_key")
            == f"observation:{assessment.assessment_id}"
        ]
        assert len(audit_messages) == 1
        assert audit_messages[0].metadata["namespace"] == namespaces.conv_bg
    finally:
        engines.close()
