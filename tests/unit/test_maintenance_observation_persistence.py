from __future__ import annotations

from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.configuration.identity import durable_claims_context
from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.maintenance.maintenance_observation import (
    ObservationFinding,
    ObservationSubject,
    assess_observation_frame,
    build_observation_frame,
)
from kogwistar_llm_wiki.maintenance.worker_observation import (
    MaintenanceObservationWorkerMixin,
)


@pytest.mark.slow
def test_observation_assessment_persists_once_on_duplicate_delivery(tmp_path, monkeypatch) -> None:
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
    send_message = engines.conversation.send_lane_message
    send_calls = 0

    def send_with_unavailable_duplicate_lookup(**kwargs):
        nonlocal send_calls
        send_calls += 1
        if send_calls == 2:
            raise NotImplementedError("backend does not expose projected-row lookup")
        return send_message(**kwargs)

    monkeypatch.setattr(
        engines.conversation,
        "send_lane_message",
        send_with_unavailable_duplicate_lookup,
    )

    try:
        with durable_claims_context({"storage_ns": namespaces.conv_bg}):
            worker._persist_observation_audit(ctx, frame, assessment)
            worker._persist_observation_audit(ctx, frame, assessment)
            revised_assessment = assess_observation_frame(
                frame,
                critic_status="succeeded",
                critic_findings=(
                    ObservationFinding(
                        code="weak_label",
                        verdict="weak_label",
                        severity="warning",
                        subject_id=subject.subject_id,
                        message="label needs a more specific concept name",
                    ),
                ),
            )
            worker._persist_observation_audit(ctx, frame, revised_assessment)

        with worker._observation_namespace(namespaces.conv_bg):
            messages = engines.conversation.read.get_nodes(
                where={"kind": "lane_message"},
                limit=100,
            )
        audit_messages = [
            node
            for node in messages
            if node.metadata.get("msg_type") == "maintenance.observation.assessment"
        ]
        assert len(audit_messages) == 2
        assert {node.metadata["idempotency_key"] for node in audit_messages} == {
            f"observation:{assessment.assessment_id}",
            f"observation:{revised_assessment.assessment_id}",
        }
        assert all(node.metadata["namespace"] == namespaces.conv_bg for node in audit_messages)
    finally:
        engines.close()


def test_observation_audit_does_not_hide_missing_persistence(tmp_path, monkeypatch) -> None:
    workspace_id = "observation-audit-missing-test"
    engines = build_in_memory_namespace_engines(tmp_path)
    worker = object.__new__(MaintenanceObservationWorkerMixin)
    worker.engines = engines
    subject = ObservationSubject(
        kind="node",
        subject_id="node:reviewed",
        workspace_id=workspace_id,
        namespace=WorkspaceNamespaces(workspace_id).curated_kg_space,
    )
    frame = build_observation_frame(subject)
    assessment = assess_observation_frame(frame)
    ctx = SimpleNamespace(
        workspace_id=workspace_id,
        job_id="maintenance-job:missing-audit",
        request_node_id="maintenance-request:missing-audit",
    )
    monkeypatch.setattr(
        engines.conversation,
        "send_lane_message",
        lambda **kwargs: (_ for _ in ()).throw(NotImplementedError("no message persisted")),
    )

    try:
        with pytest.raises(NotImplementedError, match="no message persisted"):
            worker._persist_observation_audit(ctx, frame, assessment)
    finally:
        engines.close()
