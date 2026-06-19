from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kogwistar_llm_wiki.models import IngestPipelineRequest
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def test_run_invokes_kogwistar_ingest(pipeline, ingest_request, monkeypatch):
    ingest_calls = {"source": [], "compat": []}

    original_source = pipeline.engines.kg.persist_document_graph_extraction
    original_compat = pipeline.engines.conversation.persist_document_graph_extraction

    def spy_source(*, doc_id, parsed, mode="append"):
        ingest_calls["source"].append(
            {
                "doc_id": doc_id,
                "mode": mode,
                "node_count": len(parsed.nodes),
                "edge_count": len(parsed.edges),
            }
        )
        return original_source(doc_id=doc_id, parsed=parsed, mode=mode)

    def spy_compat(*, doc_id, parsed, mode="append"):
        ingest_calls["compat"].append(
            {
                "doc_id": doc_id,
                "mode": mode,
                "node_count": len(parsed.nodes),
                "edge_count": len(parsed.edges),
            }
        )
        return original_compat(doc_id=doc_id, parsed=parsed, mode=mode)

    monkeypatch.setattr(pipeline.engines.kg, "persist_document_graph_extraction", spy_source)
    monkeypatch.setattr(pipeline.engines.conversation, "persist_document_graph_extraction", spy_compat)
    monkeypatch.setattr(
        pipeline,
        "parser",
        lambda *, document_id, title, raw_text, source_format, mode: parse_page_index_document(
            document_id=document_id,
            title=title,
            raw_text=raw_text,
            source_format=source_format,
            mode=mode,
        ),
    )

    artifacts = pipeline.run(ingest_request)
    assert ingest_calls["source"]
    assert ingest_calls["compat"]
    assert ingest_calls["source"][0]["doc_id"] == artifacts.source_document_id
    assert ingest_calls["compat"][0]["doc_id"] == artifacts.source_document_id
    assert ingest_calls["source"][0]["mode"] == "append"
    assert ingest_calls["compat"][0]["mode"] == "append"
    assert ingest_calls["source"][0]["node_count"] > 0
    assert ingest_calls["compat"][0]["node_count"] > 0

    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    with _temporary_namespace(pipeline.engines.kg, ns.source_space):
        source_nodes = pipeline.engines.kg.read.get_nodes(
            where={"doc_id": artifacts.source_document_id, "graph_space": "source"}
        )
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_fg):
        compat_nodes = pipeline.engines.conversation.read.get_nodes(
            where={"doc_id": artifacts.source_document_id}
        )
    assert source_nodes
    assert compat_nodes
    assert all(node.metadata.get("graph_space") == "source" for node in source_nodes)
    assert all(node.metadata.get("source_document_id") == artifacts.source_document_id for node in source_nodes)
    assert artifacts.operation_mode == "parse_first"
    assert artifacts.graph_status == "stable"


def test_run_maintenance_first_seeds_source_map_without_parsing(pipeline, ingest_request, monkeypatch):
    parser_called = False

    def fail_parser(**_kwargs):
        nonlocal parser_called
        parser_called = True
        raise AssertionError("maintenance_first should not parse during ingest")

    monkeypatch.setattr(pipeline, "parser", fail_parser)
    request = IngestPipelineRequest.model_validate(
        {
            **ingest_request.model_dump(),
            "operation_mode": "maintenance_first",
        }
    )

    artifacts = pipeline.run(request)

    assert parser_called is False
    assert artifacts.operation_mode == "maintenance_first"
    assert artifacts.graph_status == "seeded"
    assert artifacts.candidate_link_id == ""
    assert artifacts.promotion_candidate_id == ""

    ns = WorkspaceNamespaces(request.workspace_id)
    with _temporary_namespace(pipeline.engines.kg, ns.source_space):
        seeds = pipeline.engines.kg.read.get_nodes(
            where={"artifact_kind": "source_map_seed", "source_document_id": artifacts.source_document_id}
        )
    assert seeds
    assert seeds[0].metadata["graph_status"] == "seeded"
    assert seeds[0].metadata["operation_mode"] == "maintenance_first"
    assert seeds[0].metadata["source_map_digest"]

    jobs = pipeline.engines.conversation.jobs.list(namespace=ns.maintenance_jobs, limit=10)
    assert len(jobs) == 1
    assert jobs[0].payload["maintenance_kind"] == "document_seed_graph"


def test_maintenance_requests_for_same_source_keep_distinct_job_kinds(pipeline, ingest_request):
    source_document_id = pipeline._source_document_id(ingest_request)
    ns = WorkspaceNamespaces(ingest_request.workspace_id)

    seed_request_id = pipeline.create_maintenance_request(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=ns.conv_bg,
        maintenance_kind="document_seed_graph",
    )
    duplicate_seed_request_id = pipeline.create_maintenance_request(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=ns.conv_bg,
        maintenance_kind="document_seed_graph",
    )
    expand_request_id = pipeline.create_maintenance_request(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=ns.conv_bg,
        maintenance_kind="document_expand_parse_children",
    )

    jobs = pipeline.engines.conversation.jobs.list(namespace=ns.maintenance_jobs, limit=10)
    assert seed_request_id != expand_request_id
    assert duplicate_seed_request_id == seed_request_id
    assert len(jobs) == 2
    assert {job.payload["maintenance_kind"] for job in jobs} == {
        "document_seed_graph",
        "document_expand_parse_children",
    }
    assert {job.job_kind for job in jobs} == {
        "maintenance_job:document_seed_graph",
        "maintenance_job:document_expand_parse_children",
    }


def test_run_hybrid_keeps_parse_but_queues_graph_patch_expansion(pipeline, ingest_request, monkeypatch):
    parse_calls = 0

    def parser(**kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return parse_page_index_document(**kwargs)

    monkeypatch.setattr(pipeline, "parser", parser)
    request = IngestPipelineRequest.model_validate(
        {
            **ingest_request.model_dump(),
            "operation_mode": "hybrid",
        }
    )

    artifacts = pipeline.run(request)

    assert parse_calls == 1
    assert artifacts.operation_mode == "hybrid"
    assert artifacts.graph_status == "expanding"
    ns = WorkspaceNamespaces(request.workspace_id)
    jobs = pipeline.engines.conversation.jobs.list(namespace=ns.maintenance_jobs, limit=10)
    assert len(jobs) == 1
    assert jobs[0].payload["maintenance_kind"] == "document_expand_parse_children"
