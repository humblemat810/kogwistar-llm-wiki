from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from kogwistar_llm_wiki import __main__ as llm_wiki_cli
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.models import ObsidianBuildResult
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


@dataclass
class _FakeArtifacts:
    source_document_id: str = "source-1"
    maintenance_job_id: str = "maint-1"
    candidate_link_id: str = "cand-1"
    promotion_candidate_id: str = "promo-1"
    promoted_entity_id: str | None = "kg-1"


class _FakePipeline:
    def __init__(self, engines):
        self.engines = engines
        self.requests: list[object] = []
        self.calls: list[str] = []

    def run(self, request):
        self.requests.append(request)
        return _FakeArtifacts()

    def _record(self, name: str):
        self.calls.append(name)

    def _source_document_id(self, request):
        self._record("_source_document_id")
        return "source-1"

    def namespaces_for(self, workspace_id):
        self._record("namespaces_for")
        return WorkspaceNamespaces(workspace_id)

    def register_source(self, **kwargs):
        self._record("register_source")

    def parse_source(self, **kwargs):
        self._record("parse_source")
        return SimpleNamespace(semantic_tree=SimpleNamespace(title="Demo"))

    def translate_parse_result(self, **kwargs):
        self._record("translate_parse_result")
        return SimpleNamespace(nodes=[], edges=[])

    def persist_demo_graph_extraction(self, **kwargs):
        self._record("persist_demo_graph_extraction")

    def ingest_parse_result(self, **kwargs):
        self._record("ingest_parse_result")

    def create_maintenance_request(self, **kwargs):
        self._record("create_maintenance_request")
        return "maint-1"

    def create_candidate_link(self, **kwargs):
        self._record("create_candidate_link")
        return "cand-1"

    def create_promotion_candidate(self, **kwargs):
        self._record("create_promotion_candidate")
        return "promo-1"

    def build_obsidian_vault(self, vault_root, *, workspace_id, version=None, event_seq=None):
        self._record("build_obsidian_vault")
        return ObsidianBuildResult(vault_root=Path(vault_root), notes=7, canvases=7, dangling_links=0)

    def materialize_maintenance_designs(self):
        self._record("materialize_maintenance_designs")


def test_demo_cli_runs_end_to_end_in_one_process(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("# Demo\n\nHello\n", encoding="utf-8")
    vault = tmp_path / "vault-root"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="demo-engines", workflow=SimpleNamespace(name="workflow-engine"))

    def _fake_build_demo_engines():
        captured["demo_builder_called"] = True
        return fake_engines

    def _fake_pipeline_ctor(engines):
        assert engines is fake_engines
        pipeline = _FakePipeline(engines)
        captured["pipeline"] = pipeline
        return pipeline

    class _FakeMaintenanceWorker:
        def __init__(self, engines):
            assert engines is fake_engines

        def process_pending_jobs(self, workspace_id):
            captured["maintenance_workspace"] = workspace_id

    monkeypatch.setattr(llm_wiki_cli, "_build_demo_engines", _fake_build_demo_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.IngestPipeline", _fake_pipeline_ctor)
    monkeypatch.setattr("kogwistar_llm_wiki.worker.MaintenanceWorker", _FakeMaintenanceWorker)
    monkeypatch.setattr("kogwistar_llm_wiki.maintenance_designs.materialize_maintenance_designs", lambda workflow_engine: captured.setdefault("maintenance_designs_seeded", True))

    exit_code = llm_wiki_cli.main(
        [
            "demo",
            "--workspace",
            "demo",
            "--source",
            str(source),
            "--vault",
            str(vault),
            "--title",
            "Demo Doc",
            "--promotion-mode",
            "sync",
        ]
    )

    assert exit_code == 0
    assert vault.exists()
    assert captured["demo_builder_called"] is True
    assert captured["maintenance_workspace"] == "demo"
    assert captured["maintenance_designs_seeded"] is True

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"
    assert payload["vault"] == str(vault.resolve())
    assert payload["mode"] == "demo-memory-single-process"
    assert payload["artifacts"]["promoted_entity_id"] is None
    assert payload["vault_result"]["notes"] == 7
    pipeline = captured["pipeline"]
    assert pipeline.calls == [
        "namespaces_for",
        "_source_document_id",
        "register_source",
        "parse_source",
        "translate_parse_result",
        "persist_demo_graph_extraction",
        "ingest_parse_result",
        "create_maintenance_request",
        "create_candidate_link",
        "create_promotion_candidate",
        "build_obsidian_vault",
    ]


def test_ingest_cli_populates_workspace_from_source_file(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")
    data_dir = tmp_path / "workspace-data"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_engines(workspace_id: str, data_dir_arg: str | None, backend: str, dsn: str | None):
        captured["workspace_id"] = workspace_id
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        return fake_engines

    def _fake_pipeline_ctor(engines):
        assert engines is fake_engines
        return _FakePipeline(engines)

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.IngestPipeline", _fake_pipeline_ctor)

    exit_code = llm_wiki_cli.main(
        [
            "--data-dir",
            str(data_dir),
            "ingest",
            "--workspace",
            "demo",
            "--source",
            str(source),
            "--title",
            "Demo Doc",
            "--promotion-mode",
            "sync",
        ]
    )

    assert exit_code == 0
    assert captured["workspace_id"] == "demo"
    assert Path(captured["data_dir"]) == data_dir
    assert captured["backend"] == "chroma"
    assert captured["dsn"] is None

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"
    assert payload["source"] == str(source.resolve())
    assert payload["artifacts"]["source_document_id"] == "source-1"
    assert payload["artifacts"]["promoted_entity_id"] == "kg-1"


def test_projection_daemon_command_creates_vault_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "workspace-data"
    vault = tmp_path / "vault-root"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_engines(workspace_id: str, data_dir_arg: str | None, backend: str, dsn: str | None):
        captured["workspace_id"] = workspace_id
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        return fake_engines

    class _FakeProjectionDaemon:
        def __init__(self, *, engines, workspace_id, vault_root, poll_interval):
            captured["daemon_args"] = {
                "engines": engines,
                "workspace_id": workspace_id,
                "vault_root": vault_root,
                "poll_interval": poll_interval,
            }

        def run(self):
            return None

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.daemon.ProjectionDaemon", _FakeProjectionDaemon)

    exit_code = llm_wiki_cli.main(
        [
            "--data-dir",
            str(data_dir),
            "daemon",
            "projection",
            "--workspace",
            "demo",
            "--vault",
            str(vault),
            "--interval",
            "5",
        ]
    )

    assert exit_code == 0
    assert vault.exists()
    assert captured["workspace_id"] == "demo"
    assert Path(captured["data_dir"]) == data_dir
    assert captured["daemon_args"]["vault_root"] == str(vault)
    assert captured["backend"] == "chroma"
    assert captured["dsn"] is None


def test_ingest_cli_accepts_postgres_backend_switch(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")
    data_dir = tmp_path / "workspace-data"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_engines(workspace_id: str, data_dir_arg: str | None, backend: str, dsn: str | None):
        captured["workspace_id"] = workspace_id
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        return fake_engines

    def _fake_pipeline_ctor(engines):
        assert engines is fake_engines
        return _FakePipeline(engines)

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.IngestPipeline", _fake_pipeline_ctor)

    exit_code = llm_wiki_cli.main(
        [
            "--data-dir",
            str(data_dir),
            "--backend",
            "postgres",
            "--dsn",
            "postgresql://demo:demo@127.0.0.1:5432/demo",
            "ingest",
            "--workspace",
            "demo",
            "--source",
            str(source),
            "--title",
            "Demo Doc",
            "--promotion-mode",
            "sync",
        ]
    )

    assert exit_code == 0
    assert captured["backend"] == "postgres"
    assert captured["dsn"] == "postgresql://demo:demo@127.0.0.1:5432/demo"
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"
