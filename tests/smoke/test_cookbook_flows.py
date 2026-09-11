from __future__ import annotations

import json
from pathlib import Path

from kogwistar.engine_core import EntityEventEnvelope
from kogwistar_llm_wiki import IngestPipeline, WorkbenchApi, build_in_memory_namespace_engines
from kogwistar_llm_wiki import __main__ as llm_wiki_cli
from kogwistar_llm_wiki.agent_gateway import AgentGateway
from kogwistar_llm_wiki.archive import create_archive, restore_archive, verify_archive
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


def test_cookbook_demo_ingests_markdown_corpus_and_projects_vault(
    tmp_path: Path,
    capsys,
) -> None:
    corpus = tmp_path / "articles"
    corpus.mkdir()
    (corpus / "rollout.md").write_text(
        "# Rollout\n\nUse a staged rollout and record the decision.\n",
        encoding="utf-8",
    )
    (corpus / "rollback.md").write_text(
        "# Rollback\n\nRollback when the error budget is exceeded.\n",
        encoding="utf-8",
    )
    vault = tmp_path / "vault"

    assert llm_wiki_cli.main(
        [
            "demo",
            "--workspace",
            "cookbook-demo",
            "--source",
            str(corpus),
            "--vault",
            str(vault),
            "--source-format",
            "markdown",
            "--promotion-mode",
            "sync",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["corpus_mode"] == "directory"
    assert result["corpus_count"] == 2
    assert result["vault_result"]["vault_root"] == str(vault.resolve())
    assert list(vault.rglob("*.md"))


def test_cookbook_gateway_source_query_maintenance_and_revision_flow() -> None:
    engines = build_in_memory_namespace_engines()
    pipeline = IngestPipeline(engines)
    gateway = AgentGateway(WorkbenchApi(pipeline))
    try:
        source_uri = "https://example.test/cookbook/rollout.md"
        ingested = gateway.ingest(
            {
                "workspace_id": "cookbook-kb",
                "source_uri": source_uri,
                "title": "Rollout policy",
                "raw_text": "Use a staged rollout and record the decision.",
                "source_format": "markdown",
                "promotion_mode": "sync",
                "provenance_policy": "optional",
            }
        )
        source_id = str(ingested["artifacts"]["source_document_id"])

        search = gateway.search(
            {"workspace_id": "cookbook-kb", "query_text": "staged rollout"}
        )
        answer = gateway.query(
            {"workspace_id": "cookbook-kb", "query_text": "What is the rollout policy?"}
        )
        maintenance = gateway.maintain(
            {
                "workspace_id": "cookbook-kb",
                "topic": "rollout",
                "objective": "check related policy links",
                "max_llm_calls": 1,
                "max_steps": 1,
            }
        )
        status = gateway.status({"workspace_id": "cookbook-kb"})

        assert search["workspace_id"] == "cookbook-kb"
        assert "snapshot" in answer
        assert maintenance["status"] == "queued"
        assert maintenance["job_ids"]
        assert status["health"]["ready"] is True
        assert status["sources"]["count"] >= 1

        revised = gateway.reingest(
            {
                "workspace_id": "cookbook-kb",
                "source_document_id": source_id,
                "raw_text": "Use a staged rollout, record the decision, and review rollback criteria.",
                "source_format": "markdown",
                "provenance_policy": "optional",
            }
        )
        inspected = gateway.source(
            {"workspace_id": "cookbook-kb", "source_document_id": source_id}
        )
        assert revised["status"] == "reingested"
        assert revised["artifacts"]["source_document_id"] == source_id
        assert len(inspected["revisions"]) >= 2
    finally:
        gateway.api.close()
        engines.close()


def test_cookbook_archive_verify_dry_run_and_isolated_apply(tmp_path: Path) -> None:
    source_engines = build_in_memory_namespace_engines()
    target_engines = build_in_memory_namespace_engines()
    try:
        source_engines.kg.meta_sqlite.append_entity_event_envelope(
            EntityEventEnvelope(
                namespace=WorkspaceNamespaces("cookbook-archive").source_space,
                seq=1,
                event_id="cookbook-archive-event-1",
                entity_kind="article",
                entity_id="article-1",
                op="ADD",
                payload_json=json.dumps(
                    {"title": "Archive article", "text": "Survive isolated restore."},
                    separators=(",", ":"),
                ),
                created_at=1,
            )
        )
        archive_path = tmp_path / "cookbook-base.tar.gz"
        manifest = create_archive(
            source_engines,
            workspace_id="cookbook-archive",
            output=archive_path,
        )

        verified = verify_archive(archive_path)
        dry_run = restore_archive(
            target_engines,
            archive=archive_path,
            target_workspace_id="cookbook-recovered",
            apply=False,
        )
        assert manifest["archive_kind"] == "base"
        assert verified["archive_id"] == manifest["archive_id"]
        assert dry_run.dry_run is True
        assert dry_run.imported_events >= 1

        restored = restore_archive(
            target_engines,
            archive=archive_path,
            target_workspace_id="cookbook-recovered",
            apply=True,
        )
        assert restored.dry_run is False
        assert restored.rebuilt_vectors is True
        assert restored.imported_events >= 1
    finally:
        source_engines.close()
        target_engines.close()
