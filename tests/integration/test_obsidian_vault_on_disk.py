"""Integration tests: Obsidian vault on-disk projection.

These tests write real files to a temporary directory.  They require the
``kogwistar-obsidian-sink`` package to be installed (via bootstrap or GitHub).
They are marked ``@pytest.mark.integration`` and are excluded from the default
``pytest tests/unit/`` run.

Run with:
    pytest tests/integration/test_obsidian_vault_on_disk.py -v
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from kogwistar_llm_wiki.projection import ProjectionManager
from kogwistar_llm_wiki.projection_worker import ProjectionWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_projection_request(engines, workspace_id: str, ns, seq: int) -> None:
    """Emit a projection_request node into conv_bg so the worker can find it."""
    from kogwistar.engine_core.models import Grounding, Node, Span
    from kogwistar.id_provider import stable_id
    from kogwistar_llm_wiki.utils import _temporary_namespace

    node = Node(
        id=str(stable_id("projection_request", workspace_id, str(seq))),
        label=f"ProjectionRequest seq={seq}",
        type="entity",
        summary=f"Trigger projection seq={seq}",
        mentions=[Grounding(spans=[Span(
            collection_page_url=f"conversation/{ns.conv_bg}",
            document_page_url=f"conversation/{ns.conv_bg}",
            doc_id=f"conv:{ns.conv_bg}",
            insertion_method="test",
            page_number=1,
            start_char=0,
            end_char=1,
            excerpt="projection trigger",
            context_before="",
            context_after="",
            chunk_id=None,
            source_cluster_id=None,
        )])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "projection_request",
            "seq": seq,
        },
    )
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(node)


def _promote_entity(pipeline, ingest_request):
    """Run ingest in sync-promotion mode and return artifacts."""
    return pipeline.run(ingest_request.model_copy(update={"promotion_mode": "sync"}))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestBuildObsidianVaultOnDisk:
    """Full-rebuild path: ProjectionManager.build_obsidian_vault writes real files."""

    def test_promoted_entity_appears_as_markdown_file(
        self, pipeline, ingest_request, tmp_path
    ):
        """After promotion, build_obsidian_vault must create at least one .md file."""
        artifacts = _promote_entity(pipeline, ingest_request)
        assert artifacts.promoted_entity_id is not None, "Entity must be promoted"

        vault = tmp_path / "vault_build"
        result = pipeline.build_obsidian_vault(vault, workspace_id=ingest_request.workspace_id)

        assert result.notes >= 1, f"Expected ≥1 notes written, got {result.notes}"
        md_files = list(vault.rglob("*.md"))
        assert len(md_files) >= 1, f"No .md files found under {vault}"

    def test_vault_directory_is_created_if_missing(
        self, pipeline, ingest_request, tmp_path
    ):
        """build_obsidian_vault must create the vault root if it does not exist."""
        _promote_entity(pipeline, ingest_request)

        vault = tmp_path / "does_not_exist" / "vault"
        assert not vault.exists()

        pipeline.build_obsidian_vault(vault, workspace_id=ingest_request.workspace_id)
        assert vault.exists(), "Vault root should be created by the build"

    def test_result_fields_are_consistent_with_disk(
        self, pipeline, ingest_request, tmp_path
    ):
        """ObsidianBuildResult.notes must match actual .md file count on disk."""
        _promote_entity(pipeline, ingest_request)

        vault = tmp_path / "vault_count"
        result = pipeline.build_obsidian_vault(vault, workspace_id=ingest_request.workspace_id)

        # Allow for index.md files that are not 'note' entities
        actual_md = list(vault.rglob("*.md"))
        assert result.notes <= len(actual_md), (
            f"result.notes={result.notes} but only {len(actual_md)} .md files on disk"
        )


@pytest.mark.integration
class TestSyncObsidianVaultOnDisk:
    """Incremental-sync path: ProjectionWorker → sync_obsidian_vault writes real files."""

    def test_projection_worker_writes_files_for_queued_request(
        self, pipeline, ingest_request, namespace_engines, tmp_path
    ):
        """ProjectionWorker.process_pending_projections must produce .md files."""
        from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces

        workspace_id = ingest_request.workspace_id
        ns = WorkspaceNamespaces(workspace_id)

        # 1. Promote an entity into the KG
        artifacts = _promote_entity(pipeline, ingest_request)
        assert artifacts.promoted_entity_id is not None

        # 2. Enqueue a projection_request node
        _add_projection_request(namespace_engines, workspace_id, ns, seq=1)

        # 3. Drain the queue
        vault = tmp_path / "vault_sync"
        vault.mkdir(parents=True, exist_ok=True)

        worker = ProjectionWorker(namespace_engines)
        worker.process_pending_projections(workspace_id, str(vault))

        # 4. Assert files were written
        md_files = list(vault.rglob("*.md"))
        assert len(md_files) >= 1, (
            f"Expected ≥1 .md files after sync, found: {[f.name for f in vault.rglob('*')]}"
        )

    def test_projection_worker_advances_sequence(
        self, pipeline, ingest_request, namespace_engines, tmp_path
    ):
        """After draining seq=1, seq=2 should be the next pending item."""
        from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces

        workspace_id = ingest_request.workspace_id
        ns = WorkspaceNamespaces(workspace_id)

        _promote_entity(pipeline, ingest_request)

        # Enqueue seq=1 and seq=2
        _add_projection_request(namespace_engines, workspace_id, ns, seq=1)
        _add_projection_request(namespace_engines, workspace_id, ns, seq=2)

        vault = tmp_path / "vault_seq"
        vault.mkdir(parents=True, exist_ok=True)

        worker = ProjectionWorker(namespace_engines)
        worker.process_pending_projections(workspace_id, str(vault))

        # Both should be processed; the meta-store should record seq=2
        final_seq = worker._get_latest_projected_seq(workspace_id)
        assert final_seq == 2, f"Expected latest_projected_seq=2, got {final_seq}"

    def test_projection_worker_is_idempotent(
        self, pipeline, ingest_request, namespace_engines, tmp_path
    ):
        """Running process_pending_projections twice must not produce duplicate status events."""
        from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
        from kogwistar_llm_wiki.utils import _temporary_namespace

        workspace_id = ingest_request.workspace_id
        ns = WorkspaceNamespaces(workspace_id)

        _promote_entity(pipeline, ingest_request)
        _add_projection_request(namespace_engines, workspace_id, ns, seq=1)

        vault = tmp_path / "vault_idem"
        vault.mkdir(parents=True, exist_ok=True)

        worker = ProjectionWorker(namespace_engines)
        worker.process_pending_projections(workspace_id, str(vault))
        # Second run — queue is empty, should be a no-op
        worker.process_pending_projections(workspace_id, str(vault))

        # Only one set of status events should exist (processing + completed for seq=1)
        with _temporary_namespace(namespace_engines.conversation, ns.conv_bg):
            status_events = namespace_engines.conversation.read.get_nodes(
                where={"artifact_kind": "projection_status_event", "workspace_id": workspace_id}
            )

        statuses = [e.metadata.get("status") for e in status_events]
        completed = [s for s in statuses if s == "completed"]
        assert len(completed) == 1, (
            f"Expected exactly 1 'completed' event, got {len(completed)}: {statuses}"
        )

    def test_projection_worker_emits_status_event_nodes(
        self, pipeline, ingest_request, namespace_engines, tmp_path
    ):
        """Each processed request must produce append-only projection_status_event nodes."""
        from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
        from kogwistar_llm_wiki.utils import _temporary_namespace

        workspace_id = ingest_request.workspace_id
        ns = WorkspaceNamespaces(workspace_id)

        _promote_entity(pipeline, ingest_request)
        _add_projection_request(namespace_engines, workspace_id, ns, seq=1)

        vault = tmp_path / "vault_events"
        vault.mkdir(parents=True, exist_ok=True)

        worker = ProjectionWorker(namespace_engines)
        worker.process_pending_projections(workspace_id, str(vault))

        with _temporary_namespace(namespace_engines.conversation, ns.conv_bg):
            events = namespace_engines.conversation.read.get_nodes(
                where={"artifact_kind": "projection_status_event", "workspace_id": workspace_id}
            )

        assert len(events) >= 1, "Expected at least one projection_status_event"
        statuses = {e.metadata.get("status") for e in events}
        assert "completed" in statuses, f"Expected a 'completed' event; got: {statuses}"
        # Original request node must NOT have been mutated (still has no 'status' field)
        with _temporary_namespace(namespace_engines.conversation, ns.conv_bg):
            req_nodes = namespace_engines.conversation.read.get_nodes(
                where={"artifact_kind": "projection_request", "workspace_id": workspace_id}
            )
        assert req_nodes, "Original projection_request node must still exist (append-only)"
        for req in req_nodes:
            req_status = req.metadata.get("status")
            # The worker must never change a request node's status to 'completed' or 'failed'
            # — those transitions are recorded exclusively as projection_status_event nodes.
            # IngestPipeline may legitimately create requests with status='pending' initially.
            assert req_status not in ("completed", "failed"), (
                f"ProjectionWorker mutated projection_request node status to '{req_status}' "
                f"— should have emitted a projection_status_event instead"
            )
