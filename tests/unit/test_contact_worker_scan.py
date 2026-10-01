from __future__ import annotations

import pytest

from kogwistar_llm_wiki import WorkspaceNamespaces, build_in_memory_namespace_engines
from kogwistar_llm_wiki.disambiguation.contact_matching import (
    ContactIdentityObservation,
    ContactPointClaim,
)
from kogwistar_llm_wiki.disambiguation.service import DisambiguationService
from kogwistar_llm_wiki.worker import MaintenanceWorker


def _observation(entity_id: str, stream_id: str) -> ContactIdentityObservation:
    return ContactIdentityObservation(
        workspace_id="contact-scan-worker",
        stream_id=stream_id,
        entity_id=entity_id,
        source_document_ids=(f"doc:{entity_id}",),
        evidence_revision_ids=(f"revision:{entity_id}",),
        observed_at_ms=10,
        display_names=("Jordan Lee",),
        contact_points=(
            ContactPointClaim(
                channel="email",
                provider="test",
                value="jordan@example.test",
                verification="provider_verified",
            ),
        ),
    )


def _enqueue_scan(engines, *, job_id: str, streams: list[str]) -> None:
    workspace_id = "contact-scan-worker"
    engines.conversation.jobs.require_available(enqueue=True)
    engines.conversation.jobs.enqueue(
        job_id=job_id,
        namespace=WorkspaceNamespaces(workspace_id).maintenance_jobs,
        entity_kind="maintenance_job",
        entity_id=job_id,
        job_kind="maintenance_job:entity_disambiguation_scan",
        payload={
            "workspace_id": workspace_id,
            "request_node_id": job_id,
            "maintenance_kind": "entity_disambiguation_scan",
            "mode": "background",
            "source_stream_ids": streams,
        },
    )


@pytest.mark.ci
def test_contact_scan_worker_authorizes_then_persists_review_only_candidate() -> None:
    engines = build_in_memory_namespace_engines()
    try:
        events: list[tuple[str, str]] = []
        observations = (
            _observation("contact:mail", "mailbox:one"),
            _observation("contact:chat", "chat:one"),
        )

        def authorize(workspace_id: str, stream_id: str) -> bool:
            assert workspace_id == "contact-scan-worker"
            events.append(("authorize", stream_id))
            return True

        def provide(workspace_id: str, payload):
            assert workspace_id == "contact-scan-worker"
            assert events == [("authorize", "mailbox:one")]
            assert payload["source_stream_ids"] == ("mailbox:one",)
            # Cross-channel providers authorize their own read scope before retrieval.
            assert authorize(workspace_id, "chat:one")
            assert authorize(workspace_id, "mailbox:one")
            events.append(("provider", "called"))
            return observations

        _enqueue_scan(engines, job_id="scan-success", streams=["mailbox:one"])
        worker = MaintenanceWorker(
            engines,
            contact_observation_provider=provide,
            contact_stream_authorizer=authorize,
        )
        worker.process_pending_jobs("contact-scan-worker")

        candidates = DisambiguationService(engines).list_current_contact_candidates(
            workspace_id="contact-scan-worker",
            authorize_stream=authorize,
        )
        assert len(candidates) == 1
        assert candidates[0].entity_ids == ("contact:chat", "contact:mail")
        assert candidates[0].artifact_status.value == "pending"
        assert candidates[0].metadata["automatic_merge"] is False
        assert events[:4] == [
            ("authorize", "mailbox:one"),
            ("authorize", "chat:one"),
            ("authorize", "mailbox:one"),
            ("provider", "called"),
        ]
        job_rows = engines.conversation.meta_sqlite.list_index_jobs(
            namespace=WorkspaceNamespaces("contact-scan-worker").maintenance_jobs
        )
        assert len(job_rows) == 1
        assert job_rows[0].status == "DONE"
        worker.close()
    finally:
        engines.close()


@pytest.mark.ci
def test_contact_scan_worker_fails_closed_before_provider_and_writes() -> None:
    engines = build_in_memory_namespace_engines()
    try:
        provider_calls: list[str] = []

        def authorize(_workspace_id: str, stream_id: str) -> bool:
            return stream_id != "denied:stream"

        def provide(_workspace_id: str, _payload):
            provider_calls.append("called")
            return (_observation("contact:one", "allowed:stream"),)

        _enqueue_scan(engines, job_id="scan-denied", streams=["allowed:stream", "denied:stream"])
        worker = MaintenanceWorker(
            engines,
            contact_observation_provider=provide,
            contact_stream_authorizer=authorize,
        )
        with pytest.raises(PermissionError, match="not authorized"):
            worker.process_pending_jobs("contact-scan-worker")

        assert provider_calls == []
        assert DisambiguationService(engines).list_current_contact_candidates(
            workspace_id="contact-scan-worker",
            authorize_stream=lambda *_: True,
        ) == ()
        job_rows = engines.conversation.meta_sqlite.list_index_jobs(
            namespace=WorkspaceNamespaces("contact-scan-worker").maintenance_jobs
        )
        assert len(job_rows) == 1
        assert job_rows[0].status == "PENDING"
        worker.close()
    finally:
        engines.close()


@pytest.mark.ci
def test_contact_scan_worker_without_provider_does_not_acknowledge_job() -> None:
    engines = build_in_memory_namespace_engines()
    try:
        _enqueue_scan(engines, job_id="scan-unconfigured", streams=["mailbox:one"])
        worker = MaintenanceWorker(
            engines,
            contact_stream_authorizer=lambda *_: True,
        )
        with pytest.raises(TypeError, match="requires an observation provider"):
            worker.process_pending_jobs("contact-scan-worker")

        job_rows = engines.conversation.meta_sqlite.list_index_jobs(
            namespace=WorkspaceNamespaces("contact-scan-worker").maintenance_jobs
        )
        assert len(job_rows) == 1
        assert job_rows[0].status == "PENDING"
        assert DisambiguationService(engines).list_current_contact_candidates(
            workspace_id="contact-scan-worker",
            authorize_stream=lambda *_: True,
        ) == ()
        worker.close()
    finally:
        engines.close()
