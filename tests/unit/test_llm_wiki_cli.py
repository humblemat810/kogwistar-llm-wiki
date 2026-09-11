from __future__ import annotations

import json
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from kogwistar.engine_core.models import Edge, Grounding, Node, Span
from kogwistar_llm_wiki import __main__ as llm_wiki_cli
from kogwistar_llm_wiki.maintenance_patch_apply import apply_maintenance_patch
from kogwistar_llm_wiki.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from kogwistar_llm_wiki.models import ObsidianBuildResult
from kogwistar_llm_wiki.namespaces import GraphSpace, WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


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
        self.last_build_obsidian_vault_kwargs: dict[str, object] | None = None

    def run(self, request):
        self._record("run")
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

    def build_obsidian_vault(
        self,
        vault_root,
        *,
        workspace_id,
        graph_spaces=None,
        projection_filter=None,
        version=None,
        event_seq=None,
    ):
        self._record("build_obsidian_vault")
        self.last_build_obsidian_vault_kwargs = {
            "workspace_id": workspace_id,
            "graph_spaces": graph_spaces,
            "projection_filter": projection_filter,
            "version": version,
            "event_seq": event_seq,
        }
        return ObsidianBuildResult(vault_root=Path(vault_root), notes=7, canvases=7, dangling_links=0)

    def materialize_maintenance_designs(self):
        self._record("materialize_maintenance_designs")


@dataclass
class _FakeGraphItem:
    id: str
    metadata: dict[str, object]
    label: str = ""
    node_type: str = "entity"
    source_ids: list[str] | None = None
    target_ids: list[str] | None = None
    relation: str | None = None


class _FakeGraphRead:
    def __init__(self, engine: "_FakeGraphEngine"):
        self.engine = engine

    def get_nodes(self, *, where=None, ids=None, limit=10_000, resolve_mode=None):  # noqa: ANN001
        if ids:
            wanted = {str(item) for item in ids}
            return [item for item in self.engine.nodes_for_current_namespace() if item.id in wanted]
        where = dict(where or {})
        artifact_kind = str(where.get("artifact_kind") or "").strip()
        return [
            item
            for item in self.engine.nodes_for_current_namespace()
            if not artifact_kind or str(item.metadata.get("artifact_kind") or "") == artifact_kind
        ]

    def get_edges(self, *, where=None, limit=10_000, resolve_mode=None):  # noqa: ANN001
        return list(self.engine.edges_for_current_namespace())


class _FakeGraphEngine:
    def __init__(self, namespace_payloads: dict[str, dict[str, list[_FakeGraphItem]]]):
        self.namespace_payloads = namespace_payloads
        self.current_namespace: str | None = None
        self.read = _FakeGraphRead(self)

    def nodes_for_current_namespace(self) -> list[_FakeGraphItem]:
        namespace = self.current_namespace or ""
        payload = self.namespace_payloads.get(namespace, {})
        return list(payload.get("nodes") or [])

    def edges_for_current_namespace(self) -> list[_FakeGraphItem]:
        namespace = self.current_namespace or ""
        payload = self.namespace_payloads.get(namespace, {})
        return list(payload.get("edges") or [])


@contextmanager
def _fake_temporary_namespace(engine, namespace):
    previous = getattr(engine, "current_namespace", None)
    engine.current_namespace = namespace
    try:
        yield engine
    finally:
        engine.current_namespace = previous


def test_demo_cli_runs_end_to_end_in_one_process(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("# Demo\n\nHello\n", encoding="utf-8")
    vault = tmp_path / "vault-root"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="demo-engines", workflow=SimpleNamespace(name="workflow-engine"))

    def _fake_build_demo_engines(*, split_derived_knowledge: bool = False):
        captured["demo_builder_called"] = True
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        captured["pipeline_ctor_kwargs"] = kwargs
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
            "--debug-run-dir",
            str(tmp_path / "debug-run"),
            "--promotion-mode",
            "sync",
        ]
    )

    assert exit_code == 0
    assert vault.exists()
    assert captured["demo_builder_called"] is True
    assert captured["split_derived_knowledge"] is False
    assert captured["maintenance_workspace"] == "demo"
    assert captured["maintenance_designs_seeded"] is True
    assert Path(captured["pipeline_ctor_kwargs"]["debug_run_dir"]) == tmp_path / "debug-run"

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"
    assert payload["vault"] == str(vault.resolve())
    assert payload["mode"] == "demo-memory-single-process"
    assert payload["artifacts"]["promoted_entity_id"] == "kg-1"
    assert payload["vault_result"]["notes"] == 7
    pipeline = captured["pipeline"]
    assert pipeline.requests[0].operation_mode == "parse_first"
    assert pipeline.last_build_obsidian_vault_kwargs is not None
    assert pipeline.last_build_obsidian_vault_kwargs["graph_spaces"] == [GraphSpace.BASE_KG]
    assert pipeline.last_build_obsidian_vault_kwargs["projection_filter"] == "demo"
    assert pipeline.calls == [
        "run",
        "build_obsidian_vault",
    ]


def test_demo_cli_can_ingest_a_demo_corpus_directory(tmp_path, monkeypatch, capsys):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "doc-002.md").write_text("# B\n\nBody B\n", encoding="utf-8")
    (corpus_dir / "index.md").write_text("# Ignore me\n", encoding="utf-8")
    (corpus_dir / "doc-001.md").write_text("# A\n\nBody A\n", encoding="utf-8")
    vault = tmp_path / "vault-root"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="demo-engines", workflow=SimpleNamespace(name="workflow-engine"))

    def _fake_build_demo_engines(*, split_derived_knowledge: bool = False):
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        captured["pipeline_ctor_kwargs"] = kwargs
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
    monkeypatch.setattr(
        "kogwistar_llm_wiki.maintenance_designs.materialize_maintenance_designs",
        lambda workflow_engine: captured.setdefault("maintenance_designs_seeded", True),
    )

    exit_code = llm_wiki_cli.main(
        [
            "demo",
            "--workspace",
            "demo",
            "--source",
            str(corpus_dir),
            "--vault",
            str(vault),
            "--debug-run-dir",
            str(tmp_path / "debug-run"),
            "--promotion-mode",
            "sync",
        ]
    )

    assert exit_code == 0
    assert vault.exists()
    assert captured["maintenance_workspace"] == "demo"
    pipeline = captured["pipeline"]
    assert len(pipeline.requests) == 2
    assert [request.title for request in pipeline.requests] == ["doc 001", "doc 002"]
    assert [request.raw_text.strip() for request in pipeline.requests] == ["# A\n\nBody A", "# B\n\nBody B"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["corpus_mode"] == "directory"
    assert payload["corpus_count"] == 2
    assert len(payload["artifacts_by_source"]) == 2


def test_report_cli_summarizes_persisted_graph_quality(tmp_path, monkeypatch, capsys):
    workspace_id = "demo"
    ns = WorkspaceNamespaces(workspace_id)

    kg_payloads = {
        ns.source_space: {
            "nodes": [
                _FakeGraphItem(id="source-1", metadata={"artifact_kind": "source_doc"}, label="Source 1"),
                _FakeGraphItem(id="source-2", metadata={"artifact_kind": "source_doc"}, label="Source 2"),
            ],
            "edges": [
                _FakeGraphItem(id="source-edge-1", metadata={}, source_ids=["source-1"], target_ids=["source-2"], relation="relates_to", label="relates_to"),
            ],
        },
        ns.base_kg_space: {
            "nodes": [
                _FakeGraphItem(id="base-1", metadata={"artifact_kind": "base_node"}, label="Base 1"),
                _FakeGraphItem(id="base-2", metadata={"artifact_kind": "base_node"}, label="Base 2"),
                _FakeGraphItem(id="base-3", metadata={"artifact_kind": "base_node"}, label="Base 3"),
            ],
            "edges": [
                _FakeGraphItem(id="base-edge-1", metadata={}, source_ids=["base-1"], target_ids=["base-2"], relation="supports", label="supports"),
            ],
        },
        ns.curated_kg_space: {
            "nodes": [
                _FakeGraphItem(
                    id="curated-1",
                    metadata={
                        "artifact_kind": "maintenance_patch_artifact",
                        "patch_status": "applied",
                        "operation_count": 3,
                        "applied_count": 2,
                    },
                    label="Curated 1",
                ),
                _FakeGraphItem(
                    id="curated-2",
                    metadata={
                        "artifact_kind": "maintenance_patch_artifact",
                        "patch_status": "rejected",
                        "operation_count": 1,
                        "skipped_count": 1,
                    },
                    label="Curated 2",
                ),
                _FakeGraphItem(id="curated-3", metadata={"artifact_kind": "knowledge"}, label="Curated 3"),
            ],
            "edges": [
                _FakeGraphItem(id="curated-edge-1", metadata={}, source_ids=["curated-1"], target_ids=["curated-3"], relation="alias_of", label="alias_of"),
                _FakeGraphItem(id="curated-edge-2", metadata={}, source_ids=["curated-3"], target_ids=["curated-2"], relation="disambiguates_from", label="disambiguates_from"),
            ],
        },
    }
    conversation_payloads = {
        ns.conv_bg: {
            "nodes": [
                _FakeGraphItem(id="cand-1", metadata={"artifact_kind": "candidate_link"}),
                _FakeGraphItem(id="cand-2", metadata={"artifact_kind": "candidate_link"}),
                _FakeGraphItem(id="promo-1", metadata={"artifact_kind": "promotion_candidate"}),
                _FakeGraphItem(id="pack-1", metadata={"artifact_kind": "promotion_evidence_pack"}),
            ],
            "edges": [],
        }
    }
    workflow_payloads = {
        ns.workflow_space: {
            "nodes": [
                _FakeGraphItem(id="workflow-1", metadata={"artifact_kind": "workflow_step"}, label="Workflow 1"),
            ],
            "edges": [
                _FakeGraphItem(
                    id="workflow-edge-1",
                    metadata={},
                    source_ids=["workflow-1"],
                    target_ids=["workflow-1"],
                    relation="next_step",
                    label="next_step",
                ),
            ],
        }
    }
    wisdom_payloads = {
        ns.wisdom_space: {
            "nodes": [
                _FakeGraphItem(id="wisdom-1", metadata={"artifact_kind": "wisdom_item"}, label="Wisdom 1"),
            ],
            "edges": [
                _FakeGraphItem(
                    id="wisdom-edge-1",
                    metadata={},
                    source_ids=["wisdom-1"],
                    target_ids=["wisdom-1"],
                    relation="derived_from",
                    label="derived_from",
                ),
            ],
        }
    }
    fake_engines = SimpleNamespace(
        kg=_FakeGraphEngine(kg_payloads),
        conversation=_FakeGraphEngine(conversation_payloads),
        workflow=_FakeGraphEngine(workflow_payloads),
        wisdom=_FakeGraphEngine(wisdom_payloads),
    )
    captured: dict[str, object] = {}

    def _fake_build_engines(
        workspace_id_arg: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id_arg
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.inspection._temporary_namespace", _fake_temporary_namespace)
    monkeypatch.setattr("kogwistar_llm_wiki.review_query._temporary_namespace", _fake_temporary_namespace)

    exit_code = llm_wiki_cli.main(
        [
            "report",
            "--workspace",
            workspace_id,
            "--data-dir",
            str(tmp_path / "persisted"),
        ]
    )

    assert exit_code == 0
    assert captured["workspace_id"] == workspace_id
    assert captured["backend"] == "chroma"
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == workspace_id
    assert payload["report_scope"] == "all"
    assert payload["graph_quality"] == "stable"
    assert payload["graph_health_score"] > 0
    assert payload["maintenance_patch_report"]["patch_count"] == 2
    assert payload["maintenance_patch_report"]["applied_operations"] == 2
    assert payload["maintenance_patch_report"]["rejected_operations"] == 1
    assert payload["review_artifact_counts"] == {
        "candidate_link": 2,
        "promotion_candidate": 1,
        "promotion_evidence_pack": 1,
    }
    assert len(payload["sample_nodes"]) == 14
    assert len(payload["sample_edges"]) == 6
    assert {item["graph_space"] for item in payload["sample_nodes"]} >= {"source", "base_kg", "curated_kg", "conversation_bg", "workflow", "wisdom"}
    assert {item["graph_space"] for item in payload["sample_edges"]} >= {"source", "base_kg", "curated_kg", "workflow", "wisdom"}


def test_report_cli_can_dump_preseeded_in_memory_payload(namespace_engines, tmp_path, monkeypatch, capsys):
    workspace_id = "demo"
    ns = WorkspaceNamespaces(workspace_id)

    def _span(doc_id: str) -> Span:
        return Span.model_validate(
            {
                "collection_page_url": f"document_collection/{doc_id}",
                "document_page_url": f"document/{doc_id}",
                "doc_id": doc_id,
                "insertion_method": "manual",
                "page_number": 1,
                "start_char": 0,
                "end_char": 1,
                "excerpt": "A",
                "context_before": "",
                "context_after": "B",
                "chunk_id": None,
                "source_cluster_id": None,
            }
        )

    source_node = Node(
        label="Source Anchor",
        type="entity",
        summary="Source Anchor",
        doc_id="doc-source",
        mentions=[Grounding(spans=[_span("doc-source")])],
        metadata={"workspace_id": workspace_id, "graph_space": "source", "artifact_kind": "source_doc"},
    )
    base_node = Node(
        label="Base Anchor",
        type="entity",
        summary="Base Anchor",
        doc_id="doc-base",
        mentions=[Grounding(spans=[_span("doc-base")])],
        metadata={"workspace_id": workspace_id, "graph_space": "base_kg", "artifact_kind": "base_node"},
    )
    curated_left = Node(
        label="Curated Left",
        type="entity",
        summary="Curated Left",
        doc_id="doc-curated",
        mentions=[Grounding(spans=[_span("doc-curated")])],
        metadata={"workspace_id": workspace_id, "graph_space": "curated_kg", "artifact_kind": "maintenance_patch_artifact"},
    )
    curated_right = Node(
        label="Curated Right",
        type="entity",
        summary="Curated Right",
        doc_id="doc-curated",
        mentions=[Grounding(spans=[_span("doc-curated")])],
        metadata={"workspace_id": workspace_id, "graph_space": "curated_kg", "artifact_kind": "maintenance_patch_artifact"},
    )
    curated_edge = Edge(
        id="edge-curated-1",
        label="alias_of",
        type="relationship",
        doc_id="edge-curated-1",
        summary="Curated Left -> Curated Right",
        mentions=[Grounding(spans=[_span("doc-curated")])],
        properties={},
        source_ids=[str(curated_left.id)],
        target_ids=[str(curated_right.id)],
        relation="alias_of",
        source_edge_ids=[],
        target_edge_ids=[],
        embedding=None,
        metadata={"workspace_id": workspace_id},
        domain_id=None,
        canonical_entity_id=None,
    )

    with _temporary_namespace(namespace_engines.kg, ns.source_space):
        namespace_engines.kg.write.add_node(source_node)
    with _temporary_namespace(namespace_engines.kg, ns.base_kg_space):
        namespace_engines.kg.write.add_node(base_node)
    with _temporary_namespace(namespace_engines.kg, ns.curated_kg_space):
        namespace_engines.kg.write.add_node(curated_left)
        namespace_engines.kg.write.add_node(curated_right)
        namespace_engines.kg.write.add_edge(curated_edge)

        applied_patch = MaintenancePatch(
            patch_id="patch-applied",
            intent=MaintenanceIntent.REQUEST_REVIEW,
            scope=MaintenanceScope(workspace_id=workspace_id),
            operations=[
                MaintenancePatchOperation(
                    operation_id="op-add-node",
                    kind=MaintenanceOperationKind.ADD_NODE,
                    node_id=f"{ns.curated_kg_space}:seed-node",
                    label="Seed Node",
                    reason="seed applied patch",
                    provenance=MaintenanceProvenance(
                        source_document_id="doc-review",
                        maintenance_run_id="run-review",
                        confidence=0.9,
                    ),
                )
            ],
        )
        apply_maintenance_patch(namespace_engines.kg, applied_patch, namespace_prefix=ns.curated_kg_space)

        rejected_patch = MaintenancePatch(
            patch_id="patch-rejected",
            intent=MaintenanceIntent.DERIVE_ENTITY,
            scope=MaintenanceScope(workspace_id=workspace_id),
            operations=[
                MaintenancePatchOperation(
                    operation_id="op-invalid",
                    kind=MaintenanceOperationKind.ADD_NODE,
                    node_id="outside:node:bad",
                    provenance=MaintenanceProvenance(
                        source_document_id="doc-review",
                        maintenance_run_id="run-review",
                        confidence=0.8,
                    ),
                )
            ],
        )
        apply_maintenance_patch(namespace_engines.kg, rejected_patch, namespace_prefix=ns.curated_kg_space)

    conversation_nodes = [
        Node(
            label="Candidate Link",
            type="entity",
            summary="Candidate Link",
            doc_id="doc-review",
            mentions=[Grounding(spans=[_span("doc-review")])],
            metadata={
                "workspace_id": workspace_id,
                "conversation_lane": "background",
                "artifact_kind": "candidate_link",
            },
        ),
        Node(
            label="Promotion Candidate",
            type="entity",
            summary="Promotion Candidate",
            doc_id="doc-review",
            mentions=[Grounding(spans=[_span("doc-review")])],
            metadata={
                "workspace_id": workspace_id,
                "conversation_lane": "background",
                "artifact_kind": "promotion_candidate",
            },
        ),
        Node(
            label="Evidence Pack",
            type="entity",
            summary="Evidence Pack",
            doc_id="doc-review",
            mentions=[Grounding(spans=[_span("doc-review")])],
            metadata={
                "workspace_id": workspace_id,
                "conversation_lane": "background",
                "artifact_kind": "promotion_evidence_pack",
            },
        ),
    ]
    with _temporary_namespace(namespace_engines.conversation, ns.conv_bg):
        for node in conversation_nodes:
            namespace_engines.conversation.write.add_node(node)

    captured: dict[str, object] = {}

    def _fake_build_engines(
        workspace_id_arg: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id_arg
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return namespace_engines

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)

    exit_code = llm_wiki_cli.main(
        [
            "report",
            "--workspace",
            workspace_id,
            "--data-dir",
            str(tmp_path / "persisted"),
        ]
    )

    assert exit_code == 0
    assert captured["workspace_id"] == workspace_id
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == workspace_id
    assert payload["report_scope"] == "all"
    assert payload["graph_quality"] == "stable"
    assert payload["review_artifact_counts"] == {
        "candidate_link": 1,
        "promotion_candidate": 1,
        "promotion_evidence_pack": 1,
    }
    assert payload["maintenance_patch_report"]["patch_count"] >= 2
    assert payload["maintenance_patch_report"]["applied_operations"] >= 1
    assert payload["maintenance_patch_report"]["rejected_operations"] >= 1
    assert payload["sample_nodes"]
    assert payload["sample_edges"]
    assert any(item["artifact_kind"] == "maintenance_patch_artifact" for item in payload["sample_nodes"])


@pytest.mark.parametrize(
    ("report_scope", "expected_graph_spaces", "expected_review_counts"),
    [
        ("llm_wiki", {"source", "base_kg", "curated_kg"}, {}),
        (
            "maintenance",
            {"conversation_bg", "workflow"},
            {"candidate_link": 1, "promotion_candidate": 1, "promotion_evidence_pack": 1},
        ),
        ("thinking", {"wisdom"}, {}),
    ],
)
def test_report_cli_can_scope_lane_groups(namespace_engines, tmp_path, monkeypatch, capsys, report_scope, expected_graph_spaces, expected_review_counts):
    workspace_id = "demo"
    ns = WorkspaceNamespaces(workspace_id)

    def _span(doc_id: str) -> Span:
        return Span.model_validate(
            {
                "collection_page_url": f"document_collection/{doc_id}",
                "document_page_url": f"document/{doc_id}",
                "doc_id": doc_id,
                "insertion_method": "manual",
                "page_number": 1,
                "start_char": 0,
                "end_char": 1,
                "excerpt": "A",
                "context_before": "",
                "context_after": "B",
                "chunk_id": None,
                "source_cluster_id": None,
            }
        )

    if report_scope == "llm_wiki":
        source_node = Node(
            label="Source Anchor",
            type="entity",
            summary="Source Anchor",
            doc_id="doc-source",
            mentions=[Grounding(spans=[_span("doc-source")])],
            metadata={"workspace_id": workspace_id, "graph_space": "source", "artifact_kind": "source_doc"},
        )
        base_node = Node(
            label="Base Anchor",
            type="entity",
            summary="Base Anchor",
            doc_id="doc-base",
            mentions=[Grounding(spans=[_span("doc-base")])],
            metadata={"workspace_id": workspace_id, "graph_space": "base_kg", "artifact_kind": "base_node"},
        )
        curated_node = Node(
            label="Curated Anchor",
            type="entity",
            summary="Curated Anchor",
            doc_id="doc-curated",
            mentions=[Grounding(spans=[_span("doc-curated")])],
            metadata={"workspace_id": workspace_id, "graph_space": "curated_kg", "artifact_kind": "knowledge"},
        )
        curated_edge = Edge(
            id="edge-curated-1",
            label="alias_of",
            type="relationship",
            doc_id="edge-curated-1",
            summary="Source -> Curated",
            mentions=[Grounding(spans=[_span("doc-curated")])],
            properties={},
            source_ids=[str(source_node.id)],
            target_ids=[str(curated_node.id)],
            relation="alias_of",
            source_edge_ids=[],
            target_edge_ids=[],
            embedding=None,
            metadata={"workspace_id": workspace_id},
            domain_id=None,
            canonical_entity_id=None,
        )
        with _temporary_namespace(namespace_engines.kg, ns.source_space):
            namespace_engines.kg.write.add_node(source_node)
        with _temporary_namespace(namespace_engines.kg, ns.base_kg_space):
            namespace_engines.kg.write.add_node(base_node)
        with _temporary_namespace(namespace_engines.kg, ns.curated_kg_space):
            namespace_engines.kg.write.add_node(curated_node)
            namespace_engines.kg.write.add_edge(curated_edge)
    elif report_scope == "maintenance":
        with _temporary_namespace(namespace_engines.conversation, ns.conv_bg):
            for label, kind in [
                ("Candidate Link", "candidate_link"),
                ("Promotion Candidate", "promotion_candidate"),
                ("Evidence Pack", "promotion_evidence_pack"),
            ]:
                namespace_engines.conversation.write.add_node(
                    Node(
                        label=label,
                        type="entity",
                        summary=label,
                        doc_id="doc-maint",
                        mentions=[Grounding(spans=[_span("doc-maint")])],
                        metadata={
                            "workspace_id": workspace_id,
                            "conversation_lane": "background",
                            "artifact_kind": kind,
                        },
                    )
                )
        with _temporary_namespace(namespace_engines.workflow, ns.workflow_space):
            workflow_node = Node(
                label="Workflow Node",
                type="entity",
                summary="Workflow Node",
                doc_id="doc-workflow",
                mentions=[Grounding(spans=[_span("doc-workflow")])],
                metadata={"workspace_id": workspace_id, "graph_space": "workflow", "artifact_kind": "workflow_step"},
            )
            namespace_engines.workflow.write.add_node(workflow_node)
    else:
        with _temporary_namespace(namespace_engines.wisdom, ns.wisdom_space):
            wisdom_node = Node(
                label="Wisdom Node",
                type="entity",
                summary="Wisdom Node",
                doc_id="doc-wisdom",
                mentions=[Grounding(spans=[_span("doc-wisdom")])],
                metadata={"workspace_id": workspace_id, "graph_space": "wisdom", "artifact_kind": "wisdom_item"},
            )
            namespace_engines.wisdom.write.add_node(wisdom_node)

    captured: dict[str, object] = {}

    def _fake_build_engines(
        workspace_id_arg: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id_arg
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return namespace_engines

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.inspection._temporary_namespace", _fake_temporary_namespace)
    monkeypatch.setattr("kogwistar_llm_wiki.review_query._temporary_namespace", _fake_temporary_namespace)

    exit_code = llm_wiki_cli.main(
        [
            "report",
            "--workspace",
            workspace_id,
            "--data-dir",
            str(tmp_path / "persisted"),
            "--report-scope",
            report_scope,
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_scope"] == report_scope
    assert {item["graph_space"] for item in payload["graph_spaces"]} == expected_graph_spaces
    assert {item["graph_space"] for item in payload["sample_nodes"]} <= expected_graph_spaces
    assert payload["review_artifact_counts"] == expected_review_counts


def test_report_cli_can_dump_raw_kogwistar_payload(namespace_engines, tmp_path, monkeypatch, capsys):
    workspace_id = "demo"
    ns = WorkspaceNamespaces(workspace_id)

    def _span(doc_id: str) -> Span:
        return Span.model_validate(
            {
                "collection_page_url": f"document_collection/{doc_id}",
                "document_page_url": f"document/{doc_id}",
                "doc_id": doc_id,
                "insertion_method": "manual",
                "page_number": 1,
                "start_char": 0,
                "end_char": 1,
                "excerpt": "A",
                "context_before": "",
                "context_after": "B",
                "chunk_id": None,
                "source_cluster_id": None,
            }
        )

    source_node = Node(
        label="Source Anchor",
        type="entity",
        summary="Source Anchor",
        doc_id="doc-source",
        mentions=[Grounding(spans=[_span("doc-source")])],
        metadata={"workspace_id": workspace_id, "graph_space": "source", "artifact_kind": "source_doc"},
    )
    curated_left = Node(
        label="Curated Left",
        type="entity",
        summary="Curated Left",
        doc_id="doc-curated",
        mentions=[Grounding(spans=[_span("doc-curated")])],
        metadata={"workspace_id": workspace_id, "graph_space": "curated_kg", "artifact_kind": "knowledge"},
    )
    curated_right = Node(
        label="Curated Right",
        type="entity",
        summary="Curated Right",
        doc_id="doc-curated",
        mentions=[Grounding(spans=[_span("doc-curated")])],
        metadata={"workspace_id": workspace_id, "graph_space": "curated_kg", "artifact_kind": "knowledge"},
    )
    curated_edge = Edge(
        id="edge-curated-1",
        label="alias_of",
        type="relationship",
        doc_id="edge-curated-1",
        summary="Curated Left -> Curated Right",
        mentions=[Grounding(spans=[_span("doc-curated")])],
        properties={},
        source_ids=[str(curated_left.id)],
        target_ids=[str(curated_right.id)],
        relation="alias_of",
        source_edge_ids=[],
        target_edge_ids=[],
        embedding=None,
        metadata={"workspace_id": workspace_id},
        domain_id=None,
        canonical_entity_id=None,
    )

    with _temporary_namespace(namespace_engines.kg, ns.source_space):
        namespace_engines.kg.write.add_node(source_node)
    with _temporary_namespace(namespace_engines.kg, ns.curated_kg_space):
        namespace_engines.kg.write.add_node(curated_left)
        namespace_engines.kg.write.add_node(curated_right)
        namespace_engines.kg.write.add_edge(curated_edge)

    captured: dict[str, object] = {}

    def _fake_build_engines(
        workspace_id_arg: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id_arg
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return namespace_engines

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)

    exit_code = llm_wiki_cli.main(
        [
            "report",
            "--workspace",
            workspace_id,
            "--data-dir",
            str(tmp_path / "persisted"),
            "--report-scope",
            "llm_wiki",
            "--dump-mode",
            "raw",
            "--dump-limit-per-space",
            "5",
        ]
    )

    assert exit_code == 0
    assert captured["workspace_id"] == workspace_id
    payload = json.loads(capsys.readouterr().out)
    assert payload["dump_mode"] == "raw"
    assert payload["report_scope"] == "llm_wiki"
    assert payload["raw_node_count"] >= 3
    assert payload["raw_edge_count"] >= 1
    assert payload["raw_nodes"]
    assert payload["raw_edges"]
    first_node = payload["raw_nodes"][0]
    assert first_node["graph_space"] in {"source", "base_kg", "curated_kg"}
    assert first_node["namespace"].startswith("ws:demo:")
    assert first_node["payload"]["metadata"]["workspace_id"] == workspace_id
    assert first_node["payload"]["metadata"]["graph_space"] in {"source", "curated_kg"}
    curated_edge_dump = next(item for item in payload["raw_edges"] if item["graph_space"] == "curated_kg")
    assert curated_edge_dump["payload"]["relation"] == "alias_of"
    assert curated_edge_dump["payload"]["source_ids"]
    assert curated_edge_dump["payload"]["target_ids"]


def test_demo_cli_enables_split_derived_knowledge_hosting(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("# Demo\n\nHello\n", encoding="utf-8")
    vault = tmp_path / "vault-root"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="demo-engines", workflow=SimpleNamespace(name="workflow-engine"))

    def _fake_build_demo_engines(*, split_derived_knowledge: bool = False):
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        captured["pipeline_ctor_kwargs"] = kwargs
        pipeline = _FakePipeline(engines)
        captured["pipeline"] = pipeline
        pipeline._captured = captured
        return pipeline

    class _FakeMaintenanceWorker:
        def __init__(self, engines):
            assert engines is fake_engines

        def process_pending_jobs(self, workspace_id):
            captured["maintenance_workspace"] = workspace_id

    monkeypatch.setattr(llm_wiki_cli, "_build_demo_engines", _fake_build_demo_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.IngestPipeline", _fake_pipeline_ctor)
    monkeypatch.setattr("kogwistar_llm_wiki.worker.MaintenanceWorker", _FakeMaintenanceWorker)
    monkeypatch.setattr("kogwistar_llm_wiki.maintenance_designs.materialize_maintenance_designs", lambda workflow_engine: None)

    exit_code = llm_wiki_cli.main(
        [
            "--split-derived-knowledge",
            "demo",
            "--workspace",
            "demo",
            "--source",
            str(source),
            "--vault",
            str(vault),
            "--title",
            "Demo Doc",
            "--debug-run-dir",
            str(tmp_path / "debug-run"),
            "--promotion-mode",
            "sync",
        ]
    )

    assert exit_code == 0
    assert vault.exists()
    assert captured["split_derived_knowledge"] is True
    assert captured["maintenance_workspace"] == "demo"
    assert Path(captured["pipeline_ctor_kwargs"]["debug_run_dir"]) == tmp_path / "debug-run"


def test_cli_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as excinfo:
        llm_wiki_cli.main(["--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "python -m kogwistar_llm_wiki" in help_text
    assert "daemon" in help_text


def test_ingest_cli_populates_workspace_from_source_file(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")
    data_dir = tmp_path / "workspace-data"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_engines(
        workspace_id: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        pipeline = _FakePipeline(engines)
        captured["pipeline"] = pipeline
        return pipeline

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
    assert captured["split_derived_knowledge"] is False
    assert "persist_demo_graph_extraction" not in captured["pipeline"].calls

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"
    assert payload["source"] == str(source.resolve())
    assert payload["artifacts"]["source_document_id"] == "source-1"
    assert payload["artifacts"]["promoted_entity_id"] == "kg-1"


def test_ingest_cli_uses_kogwistar_data_dir_when_explicit_dir_is_omitted(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")
    data_dir = tmp_path / "env-data"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_persistent_namespace_engines(*, base_dir, split_derived_knowledge=False):
        captured["base_dir"] = base_dir
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_build_postgres_namespace_engines(*, base_dir, dsn, split_derived_knowledge=False):
        captured["base_dir"] = base_dir
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        pipeline = _FakePipeline(engines)
        captured["pipeline"] = pipeline
        return pipeline

    monkeypatch.setenv("KOGWISTAR_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        "kogwistar_llm_wiki.ingest_pipeline.build_persistent_namespace_engines",
        _fake_build_persistent_namespace_engines,
    )
    monkeypatch.setattr(
        "kogwistar_llm_wiki.ingest_pipeline.build_postgres_namespace_engines",
        _fake_build_postgres_namespace_engines,
    )
    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.IngestPipeline", _fake_pipeline_ctor)

    exit_code = llm_wiki_cli.main(
        [
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
    assert Path(captured["base_dir"]) == data_dir
    assert captured["split_derived_knowledge"] is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"


def test_projection_daemon_command_creates_vault_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "workspace-data"
    vault = tmp_path / "vault-root"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_engines(
        workspace_id: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
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


def test_projection_daemon_uses_kogwistar_data_dir_when_explicit_dir_is_omitted(tmp_path, monkeypatch):
    data_dir = tmp_path / "env-data"
    vault = tmp_path / "vault-root"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_persistent_namespace_engines(*, base_dir, split_derived_knowledge=False):
        captured["base_dir"] = base_dir
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_build_postgres_namespace_engines(*, base_dir, dsn, split_derived_knowledge=False):
        captured["base_dir"] = base_dir
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
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

    monkeypatch.setenv("KOGWISTAR_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        "kogwistar_llm_wiki.ingest_pipeline.build_persistent_namespace_engines",
        _fake_build_persistent_namespace_engines,
    )
    monkeypatch.setattr(
        "kogwistar_llm_wiki.ingest_pipeline.build_postgres_namespace_engines",
        _fake_build_postgres_namespace_engines,
    )
    monkeypatch.setattr("kogwistar_llm_wiki.daemon.ProjectionDaemon", _FakeProjectionDaemon)

    exit_code = llm_wiki_cli.main(
        [
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
    assert Path(captured["base_dir"]) == data_dir
    assert captured["daemon_args"]["vault_root"] == str(vault)


def test_ingest_cli_accepts_postgres_backend_switch(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")
    data_dir = tmp_path / "workspace-data"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_engines(
        workspace_id: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        pipeline = _FakePipeline(engines)
        captured["pipeline"] = pipeline
        return pipeline

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
    assert captured["split_derived_knowledge"] is False
    assert "persist_demo_graph_extraction" not in captured["pipeline"].calls
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"


def test_persistent_cli_explicit_data_dir_wins_over_env(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")
    env_data_dir = tmp_path / "env-data"
    cli_data_dir = tmp_path / "cli-data"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_persistent_namespace_engines(*, base_dir, split_derived_knowledge=False):
        captured["base_dir"] = base_dir
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_build_postgres_namespace_engines(*, base_dir, dsn, split_derived_knowledge=False):
        captured["base_dir"] = base_dir
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        pipeline = _FakePipeline(engines)
        captured["pipeline"] = pipeline
        return pipeline

    monkeypatch.setenv("KOGWISTAR_DATA_DIR", str(env_data_dir))
    monkeypatch.setattr(
        "kogwistar_llm_wiki.ingest_pipeline.build_persistent_namespace_engines",
        _fake_build_persistent_namespace_engines,
    )
    monkeypatch.setattr(
        "kogwistar_llm_wiki.ingest_pipeline.build_postgres_namespace_engines",
        _fake_build_postgres_namespace_engines,
    )
    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.IngestPipeline", _fake_pipeline_ctor)

    exit_code = llm_wiki_cli.main(
        [
            "--data-dir",
            str(cli_data_dir),
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
    assert Path(captured["base_dir"]) == cli_data_dir
    assert captured["split_derived_knowledge"] is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"


def test_persistent_cli_requires_data_dir_or_env(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")

    monkeypatch.delenv("KOGWISTAR_DATA_DIR", raising=False)

    with pytest.raises(ValueError, match="--data-dir or KOGWISTAR_DATA_DIR"):
        llm_wiki_cli.main(
            [
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


def test_ingest_cli_enables_split_derived_knowledge_hosting(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("Alpha\nBeta\n", encoding="utf-8")
    data_dir = tmp_path / "workspace-data"
    captured: dict[str, object] = {}

    fake_engines = SimpleNamespace(name="engines")

    def _fake_build_engines(
        workspace_id: str,
        data_dir_arg: str | None,
        backend: str,
        dsn: str | None,
        *,
        split_derived_knowledge: bool = False,
    ):
        captured["workspace_id"] = workspace_id
        captured["data_dir"] = data_dir_arg
        captured["backend"] = backend
        captured["dsn"] = dsn
        captured["split_derived_knowledge"] = split_derived_knowledge
        return fake_engines

    def _fake_pipeline_ctor(engines, **kwargs):
        assert engines is fake_engines
        pipeline = _FakePipeline(engines)
        captured["pipeline"] = pipeline
        return pipeline

    monkeypatch.setattr(llm_wiki_cli, "_build_engines", _fake_build_engines)
    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.IngestPipeline", _fake_pipeline_ctor)

    exit_code = llm_wiki_cli.main(
        [
            "--data-dir",
            str(data_dir),
            "--split-derived-knowledge",
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
    assert captured["split_derived_knowledge"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_id"] == "demo"
