from __future__ import annotations

from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.daemon import MaintenanceDaemon, ProjectionDaemon
from kogwistar_llm_wiki.ingest_pipeline import (
    IngestPipeline,
    IngestPipelineRequest,
    build_persistent_namespace_engines,
)
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


def _request() -> IngestPipelineRequest:
    return IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days. Either party may terminate with notice.",
        promotion_mode="sync",
    )


def _expire_claimed_job_lease(engines, job_id: str) -> None:
    with engines.conversation.meta_sqlite.transaction() as conn:
        conn.execute(
            "UPDATE index_jobs SET status = 'DOING', lease_until = ?, updated_at = ? WHERE job_id = ?",
            (0, 0, str(job_id)),
        )


def test_maintenance_daemon_startup_repairs_missing_lane_projection_rows(tmp_path):
    base_dir = tmp_path / "maintenance-daemon-recovery"
    engines = build_persistent_namespace_engines(base_dir)
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)
    pipeline.run(_request())

    ns = WorkspaceNamespaces("demo")
    durable_namespace = str(
        getattr(engines.conversation, "namespace", "conversation") or "conversation"
    )
    assert (
        engines.conversation.meta_sqlite.clear_projected_lane_messages(
            durable_namespace
        )
        == 1
    )

    daemon = MaintenanceDaemon(engines, "demo", poll_interval=0.01)
    recovery = daemon.recover_startup_state()

    service_rows = engines.conversation.service_health.list_services(workspace_id="demo")
    maintenance_health = next(
        row for row in service_rows if row["service_kind"] == "maintenance_daemon"
    )
    assert maintenance_health["owner_app"] == "kogwistar-llm-wiki"
    assert maintenance_health["llm_assisted"] is True
    assert maintenance_health["deterministic"] is False
    assert [surface.surface_kind for surface in recovery.app_surfaces] == []
    assert [
        item.daemon_id for item in recovery.daemon_health
    ] == ["kogwistar-llm-wiki:demo:maintenance_daemon"]

    repaired = {item.namespace: item for item in recovery.repaired_lane_projections}
    assert repaired[durable_namespace].repaired_count == 1
    assert repaired[ns.conv_bg].repaired_count == 0
    assert repaired[ns.conv_fg].repaired_count == 0

    daemon._worker.process_pending_jobs("demo")

    maintenance_rows = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:worker:maintenance"
    )
    foreground_rows = engines.conversation.list_projected_lane_messages(
        inbox_id="inbox:foreground"
    )
    assert len(maintenance_rows) == 1
    assert maintenance_rows[0].status == "completed"
    assert len(foreground_rows) == 1
    assert foreground_rows[0].msg_type == "reply.maintenance.completed"


def test_projection_daemon_startup_recovery_is_bounded(tmp_path):
    base_dir = tmp_path / "projection-daemon-recovery"
    engines = build_persistent_namespace_engines(base_dir)
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)
    artifacts = pipeline.run(_request())

    ns = WorkspaceNamespaces("demo")
    meta = engines.conversation.meta_sqlite
    meta.replace_named_projection(
        namespace=ns.projection_manifest,
        key="demo",
        payload={
            "workspace_id": "demo",
            "projected_ids": [str(artifacts.promoted_entity_id or "")],
            "status": "ready",
        },
        last_authoritative_seq=7,
        last_materialized_seq=7,
        projection_schema_version=1,
        materialization_status="ready",
    )
    manifest_before = meta.get_named_projection(ns.projection_manifest, "demo")
    assert manifest_before is not None
    durable_namespace = str(
        getattr(engines.conversation, "namespace", "conversation") or "conversation"
    )
    assert meta.clear_projected_lane_messages(durable_namespace) == 1

    vault_root = tmp_path / "projection-vault"
    daemon = ProjectionDaemon(
        engines=engines,
        workspace_id="demo",
        vault_root=str(vault_root),
        poll_interval=0.01,
    )
    recovery = daemon.recover_startup_state()

    service_rows = engines.conversation.service_health.list_services(workspace_id="demo")
    projection_health = next(
        row for row in service_rows if row["service_kind"] == "projection_daemon"
    )
    assert projection_health["owner_app"] == "kogwistar-llm-wiki"
    assert projection_health["llm_assisted"] is False
    assert projection_health["deterministic"] is True
    assert [surface.surface_kind for surface in recovery.app_surfaces] == [
        "projection_manifest",
        "vault_materialization",
    ]
    assert [
        item.daemon_id for item in recovery.daemon_health
    ] == ["kogwistar-llm-wiki:demo:projection_daemon"]

    repaired = {item.namespace: item for item in recovery.repaired_lane_projections}
    assert repaired[durable_namespace].repaired_count == 1
    manifest_after = meta.get_named_projection(ns.projection_manifest, "demo")
    assert manifest_after == manifest_before
    assert not vault_root.exists()


def test_restart_after_interrupt_reclaims_expired_job_lease(tmp_path, monkeypatch):
    """Lease recovery should replay durable work without depending on unrelated workflow lookup state."""
    base_dir = tmp_path / "maintenance-daemon-restart"
    engines = build_persistent_namespace_engines(base_dir)
    pipeline = IngestPipeline(engines)
    monkeypatch.setattr(pipeline, "_get_existing_node", lambda *args, **kwargs: None)
    materialize_maintenance_designs(engines.workflow)
    pipeline.run(_request())

    ns = WorkspaceNamespaces("demo")
    claimed = engines.conversation.jobs.claim(
        namespace=ns.maintenance_jobs,
        limit=1,
        lease_seconds=60,
    )
    assert len(claimed) == 1
    _expire_claimed_job_lease(engines, claimed[0].job_id)

    restarted = build_persistent_namespace_engines(base_dir)
    daemon = MaintenanceDaemon(restarted, "demo", poll_interval=0.01)
    recovery = daemon.recover_startup_state()

    assert recovery.repaired_count >= 0
    monkeypatch.setattr(
        daemon._worker.runtime,
        "run",
        lambda **kwargs: SimpleNamespace(status="succeeded"),
    )
    daemon._worker.process_pending_jobs("demo")

    done_jobs = restarted.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        status="DONE",
        limit=10,
    )
    foreground_rows = restarted.conversation.list_projected_lane_messages(
        inbox_id="inbox:foreground"
    )
    assert len(done_jobs) == 1
    assert len(foreground_rows) == 1
    assert foreground_rows[0].msg_type == "reply.maintenance.completed"


def test_maintenance_daemon_stop_exits_after_current_poll_cycle(monkeypatch):
    calls: list[str] = []

    class _FakeWorker:
        def __init__(self, engines):
            del engines

        def process_pending_jobs(self, workspace_id: str):
            calls.append(workspace_id)
            daemon.stop()

    monkeypatch.setattr("kogwistar_llm_wiki.daemon.MaintenanceWorker", _FakeWorker)
    daemon = MaintenanceDaemon(SimpleNamespace(), "demo", poll_interval=0.01)
    monkeypatch.setattr(
        daemon,
        "recover_startup_state",
        lambda: SimpleNamespace(
            workspace_id="demo",
            repaired_count=0,
            scanned_count=0,
            repaired_lane_projections=(),
            queues=(),
            lane_rows=(),
            checkpoints=(),
            run_history=(),
            dead_letters=(),
            findings=(),
        ),
    )

    daemon.run()

    assert calls == ["demo"]


def test_daemon_poll_updates_durable_service_health_without_graph_heartbeat_spam(tmp_path):
    base_dir = tmp_path / "maintenance-daemon-health"
    engines = build_persistent_namespace_engines(base_dir)
    pipeline = IngestPipeline(engines)
    materialize_maintenance_designs(engines.workflow)
    pipeline.run(_request())

    daemon = MaintenanceDaemon(engines, "demo", poll_interval=0.01)
    daemon.recover_startup_state()
    before_events = engines.conversation.read.get_nodes(
        where={"entity_type": "service_health_event"},
        limit=10_000,
    )

    daemon._worker.process_pending_jobs("demo")
    from kogwistar_llm_wiki.daemon import _heartbeat_service_health

    _heartbeat_service_health(
        engines,
        workspace_id="demo",
        service_kind="maintenance_daemon",
        instance_id=daemon._instance_id,
    )

    rows = engines.conversation.service_health.list_services(workspace_id="demo")
    health = next(row for row in rows if row["service_kind"] == "maintenance_daemon")
    assert health["instance_id"] == daemon._instance_id
    assert health["last_seen_ms"] is not None
    assert health["status"] == "healthy"

    after_events = engines.conversation.read.get_nodes(
        where={"entity_type": "service_health_event"},
        limit=10_000,
    )
    assert len(after_events) == len(before_events)


def test_startup_recovery_restores_missing_service_health_projection(tmp_path):
    base_dir = tmp_path / "maintenance-daemon-health-repair"
    engines = build_persistent_namespace_engines(base_dir)
    daemon = MaintenanceDaemon(engines, "demo", poll_interval=0.01)
    recovery = daemon.recover_startup_state()
    assert recovery.daemon_health

    engines.conversation.meta_sqlite.clear_named_projection(
        "service_health",
        "demo|conversation|kogwistar-llm-wiki:demo:maintenance_daemon",
    )
    assert (
        engines.conversation.service_health.get_service(
            "kogwistar-llm-wiki:demo:maintenance_daemon",
            workspace_id="demo",
            namespace="conversation",
        )
        is None
    )

    repaired = daemon.recover_startup_state()
    assert [
        item.daemon_id for item in repaired.daemon_health
    ] == ["kogwistar-llm-wiki:demo:maintenance_daemon"]


def test_startup_recovery_does_not_touch_service_supervisor_controls(tmp_path, monkeypatch):
    """Startup recovery may inspect service health, but it must not orchestrate supervised services."""
    base_dir = tmp_path / "recovery-no-supervisor-touch"
    engines = build_persistent_namespace_engines(base_dir)
    daemon = MaintenanceDaemon(engines, "demo", poll_interval=0.01)

    supervisor = SimpleNamespace(
        tick=lambda *args, **kwargs: pytest.fail("recovery should not tick ServiceSupervisor"),
        bootstrap=lambda *args, **kwargs: pytest.fail("recovery should not bootstrap ServiceSupervisor"),
        trigger_service=lambda *args, **kwargs: pytest.fail("recovery should not trigger ServiceSupervisor"),
    )
    engines.conversation.service_supervisor = supervisor

    recovery = daemon.recover_startup_state()
    assert recovery.daemon_health
