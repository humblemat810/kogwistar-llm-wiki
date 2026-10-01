from __future__ import annotations

import pytest

from kogwistar_llm_wiki import (
    IngestPipeline,
    WorkbenchApi,
    WorkspaceNamespaces,
    build_in_memory_namespace_engines,
)
from kogwistar_llm_wiki.daemon import MaintenanceDaemon
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


@pytest.mark.ci
def test_contact_scan_overflow_fails_before_partial_candidate_persistence() -> None:
    engines = build_in_memory_namespace_engines()
    try:
        _enqueue_scan(engines, job_id="scan-overflow", streams=["mailbox:one"])
        observations = tuple(
            _observation(f"contact:{index:03d}", "mailbox:one")
            for index in range(251)
        )
        worker = MaintenanceWorker(
            engines,
            contact_observation_provider=lambda *_: observations,
            contact_stream_authorizer=lambda *_: True,
        )
        with pytest.raises(ValueError, match="observation limit"):
            worker.process_pending_jobs("contact-scan-worker")

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
def test_registered_optional_contact_adapter_flows_into_maintenance_worker() -> None:
    engines = build_in_memory_namespace_engines()
    try:
        api = WorkbenchApi(
            IngestPipeline(engines),
            resource_authorizer=lambda *_: True,
        )
        observations = (
            _observation("contact:mail", "mailbox:registered"),
            _observation("contact:slack", "slack:registered"),
        )
        observed_authorizers: list[tuple[str, str]] = []

        def authorize(workspace_id: str, stream_id: str) -> bool:
            observed_authorizers.append((workspace_id, stream_id))
            return workspace_id == "contact-scan-worker" and stream_id in {
                "mailbox:registered",
                "slack:registered",
            }

        def scan_source_for(stream_id: str, observation: ContactIdentityObservation):
            def scan_source(workspace_id: str, payload, authorize_stream):
                # Trigger is only the new email stream; each adapter reads its
                # own registered stream after authorizing it.
                assert payload["source_stream_ids"] == ("mailbox:registered",)
                assert authorize_stream(workspace_id, stream_id)
                return (observation,)

            return scan_source

        for source_id, observation in zip(("email", "slack"), observations, strict=True):
            stream_id = observation.stream_id
            api.register_contact_observation_source(
                source_id,
                provider=lambda *_: (),
                owns_stream=lambda workspace, stream, owned=stream_id: (
                    workspace == "contact-scan-worker" and stream == owned
                ),
                authorize_stream=authorize,
                scan_provider=scan_source_for(stream_id, observation),
            )
        assert set(api.contact_scan_observation_providers()) == {"email", "slack"}
        _enqueue_scan(engines, job_id="scan-registered", streams=["mailbox:registered"])
        worker = MaintenanceWorker(
            engines,
            contact_observation_providers=api.contact_scan_observation_providers(),
            contact_stream_authorizer=api.authorize_contact_stream,
        )
        worker.process_pending_jobs("contact-scan-worker")

        candidates = DisambiguationService(engines).list_current_contact_candidates(
            workspace_id="contact-scan-worker",
            authorize_stream=api.authorize_contact_stream,
        )
        assert len(candidates) == 1
        assert candidates[0].entity_ids == ("contact:mail", "contact:slack")
        assert candidates[0].metadata["automatic_merge"] is False
        assert {stream for _workspace, stream in observed_authorizers} == {
            "mailbox:registered",
            "slack:registered",
        }
        worker.close()
        api.close()
    finally:
        engines.close()


@pytest.mark.ci
def test_maintenance_daemon_accepts_optional_registered_scan_sources(tmp_path) -> None:
    engines = build_in_memory_namespace_engines()
    daemon = None
    try:
        daemon = MaintenanceDaemon(
            engines,
            "contact-scan-worker",
            data_dir=tmp_path,
            contact_observation_providers={"email": lambda *_: ()},
            contact_stream_authorizer=lambda *_: True,
        )
        assert callable(daemon._worker.contact_observation_provider)
        assert daemon._worker.contact_stream_authorizer("workspace", "stream") is True
    finally:
        if daemon is not None:
            daemon._worker.close()
        engines.close()
