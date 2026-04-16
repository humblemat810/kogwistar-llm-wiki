"""Integration tests for Obsidian vault projection on disk."""

from __future__ import annotations

import pytest

from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.projection_worker import ProjectionWorker


def _sync_request(ingest_request):
    return ingest_request.model_copy(update={"promotion_mode": "sync"})


@pytest.mark.integration
class TestBuildObsidianVaultOnDisk:
    def test_promoted_entity_appears_as_markdown_file(self, pipeline, ingest_request, tmp_path):
        artifacts = pipeline.run(_sync_request(ingest_request))
        assert artifacts.promoted_entity_id is not None

        vault = tmp_path / "vault_build"
        result = pipeline.build_obsidian_vault(vault, workspace_id=ingest_request.workspace_id)

        assert result.notes >= 1
        assert list(vault.rglob("*.md"))

    def test_vault_directory_is_created_if_missing(self, pipeline, ingest_request, tmp_path):
        pipeline.run(_sync_request(ingest_request))

        vault = tmp_path / "does_not_exist" / "vault"
        assert not vault.exists()

        pipeline.build_obsidian_vault(vault, workspace_id=ingest_request.workspace_id)
        assert vault.exists()


@pytest.mark.integration
class TestSyncObsidianVaultOnDisk:
    def test_projection_worker_writes_files_and_marks_jobs_done(
        self, pipeline, ingest_request, tmp_path
    ):
        workspace_id = ingest_request.workspace_id
        ns = WorkspaceNamespaces(workspace_id)

        artifacts = pipeline.run(_sync_request(ingest_request))
        assert artifacts.promoted_entity_id is not None

        vault = tmp_path / "vault_sync"
        vault.mkdir(parents=True, exist_ok=True)

        worker = ProjectionWorker(pipeline.engines)
        worker.process_pending_projections(workspace_id, str(vault))

        md_files = list(vault.rglob("*.md"))
        assert len(md_files) >= 1

        done_jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
            namespace=ns.projection_jobs,
            status="DONE",
            limit=10,
        )
        assert len(done_jobs) == 1

        manifest = pipeline.engines.conversation.meta_sqlite.get_named_projection(
            ns.projection_manifest,
            workspace_id,
        )
        assert manifest is not None
        assert manifest["materialization_status"] == "ready"
        assert artifacts.promoted_entity_id in manifest["payload"]["projected_ids"]

    def test_projection_worker_is_idempotent_when_queue_is_empty(
        self, pipeline, ingest_request, tmp_path
    ):
        workspace_id = ingest_request.workspace_id

        pipeline.run(_sync_request(ingest_request))

        vault = tmp_path / "vault_idem"
        vault.mkdir(parents=True, exist_ok=True)

        worker = ProjectionWorker(pipeline.engines)
        worker.process_pending_projections(workspace_id, str(vault))
        first_count = len(list(vault.rglob("*.md")))

        worker.process_pending_projections(workspace_id, str(vault))
        second_count = len(list(vault.rglob("*.md")))

        assert first_count == second_count
