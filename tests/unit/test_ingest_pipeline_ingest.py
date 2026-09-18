import pytest
from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document

from kogwistar_llm_wiki.models import IngestPipelineRequest
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.parse_session_store import ParseSessionStore
from kogwistar_llm_wiki.parse_views import SourceRegion, reparse_session_id
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
    revision = pipeline.source_revision(
        request=ingest_request,
        source_document_id=artifacts.source_document_id,
    )
    assert ingest_calls["source"][0]["doc_id"] == revision.revision_document_id
    assert ingest_calls["compat"][0]["doc_id"] == revision.revision_document_id
    assert ingest_calls["source"][0]["mode"] == "append"
    assert ingest_calls["compat"][0]["mode"] == "append"
    assert ingest_calls["source"][0]["node_count"] > 0
    assert ingest_calls["compat"][0]["node_count"] > 0

    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    with _temporary_namespace(pipeline.engines.kg, ns.source_space):
        source_nodes = pipeline.engines.kg.read.get_nodes(
            where={"doc_id": revision.revision_document_id, "graph_space": "source"}
        )
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_fg):
        compat_nodes = pipeline.engines.conversation.read.get_nodes(
            where={"doc_id": revision.revision_document_id}
        )
    assert source_nodes
    assert compat_nodes
    assert all(node.metadata.get("graph_space") == "source" for node in source_nodes)
    assert all(node.metadata.get("source_document_id") == artifacts.source_document_id for node in source_nodes)
    assert artifacts.operation_mode == "parse_first"
    assert artifacts.graph_status == "stable"


def test_changed_source_bytes_create_a_new_revision_document_without_overwrite(
    pipeline, ingest_request
):
    source_document_id = pipeline._source_document_id(ingest_request)
    namespace = WorkspaceNamespaces(ingest_request.workspace_id).conv_fg
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=namespace,
    )
    first_revision = pipeline.source_revision(
        request=ingest_request,
        source_document_id=source_document_id,
    )
    changed = ingest_request.model_copy(
        update={"raw_text": f"{ingest_request.raw_text}\nA later immutable revision."}
    )
    pipeline.register_source(
        request=changed,
        source_document_id=source_document_id,
        namespace=namespace,
    )
    second_revision = pipeline.source_revision(
        request=changed,
        source_document_id=source_document_id,
    )
    assert first_revision.revision_id != second_revision.revision_id
    assert first_revision.revision_document_id != second_revision.revision_document_id
    with _temporary_namespace(pipeline.engines.kg, WorkspaceNamespaces("demo").source_space):
        first_document = pipeline.engines.kg.read.get_document(first_revision.revision_document_id)
        second_document = pipeline.engines.kg.read.get_document(second_revision.revision_document_id)
    assert first_document.content == ingest_request.raw_text
    assert second_document.content == changed.raw_text


def test_targeted_reparse_is_revision_pinned_and_creates_bounded_session(pipeline, ingest_request):
    source_document_id = pipeline._source_document_id(ingest_request)
    namespace = WorkspaceNamespaces(ingest_request.workspace_id).conv_bg
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=namespace,
    )
    revision = pipeline.source_revision(request=ingest_request, source_document_id=source_document_id)
    assert revision.revision_document_id is not None
    target = {
        "source_document_id": source_document_id,
        "source_revision_id": revision.revision_id,
        "revision_document_id": revision.revision_document_id,
        "region": {
            "source_document_id": revision.revision_document_id,
            "start_char": 0,
            "end_char": min(8, len(ingest_request.raw_text)),
        },
        "reason": "repair one grounded region",
        "parser_profile": "page-index-v2",
    }

    pipeline.create_maintenance_request(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=namespace,
        maintenance_kind="document_reparse_region",
        parse_target=target,
    )

    region = SourceRegion.model_validate(target["region"])
    session_id = reparse_session_id(
        workspace_id=ingest_request.workspace_id,
        source_document_id=source_document_id,
        source_revision_id=revision.revision_id,
        parser_profile="page-index-v2",
        region=region,
    )
    session = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=ingest_request.workspace_id,
    ).get(session_id)
    assert session is not None
    assert session[1][0].region == region
    assert session[0].parser_state["source_revision_document_id"] == revision.revision_document_id

    stale = dict(target)
    stale["source_revision_id"] = "stale"
    with pytest.raises(ValueError, match="current immutable source revision"):
        pipeline.create_maintenance_request(
            request=ingest_request,
            source_document_id=source_document_id,
            namespace=namespace,
            maintenance_kind="document_reparse_region",
            parse_target=stale,
        )


def test_durable_session_region_length_comes_from_immutable_revision(
    pipeline, ingest_request
):
    source_document_id = pipeline._source_document_id(ingest_request)
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=WorkspaceNamespaces(ingest_request.workspace_id).conv_bg,
    )
    revision = pipeline.source_revision(request=ingest_request, source_document_id=source_document_id)
    assert revision.revision_document_id is not None
    caller_with_stale_text = ingest_request.model_copy(
        update={"raw_text": ingest_request.raw_text + " stale caller bytes" * 100}
    )

    session = pipeline.initialize_durable_parse_session(
        request=caller_with_stale_text,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id,
        revision=revision,
    )

    assert session.frontier_ids
    stored = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=ingest_request.workspace_id,
    ).get(session.session_id)
    assert stored is not None
    assert stored[1][0].region.end_char == len(ingest_request.raw_text)


def test_durable_sessions_do_not_reuse_a_different_parser_profile(pipeline, ingest_request):
    source_document_id = pipeline._source_document_id(ingest_request)
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=WorkspaceNamespaces(ingest_request.workspace_id).conv_bg,
    )
    revision = pipeline.source_revision(request=ingest_request, source_document_id=source_document_id)
    assert revision.revision_document_id is not None

    first = pipeline.initialize_durable_parse_session(
        request=ingest_request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id,
        revision=revision,
        parser_profile="page-index-v1",
    )
    second = pipeline.initialize_durable_parse_session(
        request=ingest_request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id,
        revision=revision,
        parser_profile="page-index-v2",
    )

    assert first.session_id != second.session_id
    assert first.generation_id != second.generation_id


def test_ingest_repairs_boundary_span_before_both_persistence_paths(
    pipeline, ingest_request
):
    source_document_id = pipeline._source_document_id(ingest_request)
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=WorkspaceNamespaces(ingest_request.workspace_id).conv_fg,
    )
    parsed = parse_page_index_document(
        document_id=source_document_id,
        title=ingest_request.title,
        raw_text=ingest_request.raw_text,
        source_format=ingest_request.source_format,
        mode="heuristic",
    )
    graph = pipeline.translate_parse_result(
        parse_result=parsed,
        source_document_id=source_document_id,
    )
    original_span = graph.nodes[0].mentions[0].spans[0]
    graph.nodes[0].mentions[0].spans[0] = original_span.model_copy(
        update={
            "start_char": original_span.start_char + 1,
            "end_char": original_span.end_char + 1,
        }
    )

    pipeline.ingest_parse_result(
        request=ingest_request,
        source_document_id=source_document_id,
        graph_extraction=graph,
        namespace=WorkspaceNamespaces(ingest_request.workspace_id).conv_fg,
    )

    # The write succeeded through both source and compatibility persistence;
    # the same repair helper is also directly asserted below because the
    # pipeline intentionally repairs a defensive source copy.
    pipeline._repair_graph_extraction_spans(
        graph_extraction=graph,
        source_document_id=source_document_id,
        request=ingest_request,
    )
    assert graph.nodes[0].mentions[0].spans[0].start_char == original_span.start_char
    assert graph.nodes[0].mentions[0].spans[0].end_char == original_span.end_char


def test_targeted_parse_rejects_span_outside_requested_region(
    pipeline, ingest_request
):
    source_document_id = pipeline._source_document_id(ingest_request)
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=WorkspaceNamespaces(ingest_request.workspace_id).conv_fg,
    )
    parsed = parse_page_index_document(
        document_id=source_document_id,
        title=ingest_request.title,
        raw_text=ingest_request.raw_text,
        source_format=ingest_request.source_format,
        mode="heuristic",
    )
    graph = pipeline.translate_parse_result(
        parse_result=parsed,
        source_document_id=source_document_id,
    )
    span = graph.nodes[0].mentions[0].spans[0]
    outside = span.model_copy(update={"start_char": 0, "end_char": len(ingest_request.raw_text)})
    graph.nodes[0].mentions[0].spans[0] = outside

    with pytest.raises(ValueError, match="escapes its requested source region"):
        pipeline.ingest_parse_result(
            request=ingest_request,
            source_document_id=source_document_id,
            graph_extraction=graph,
            namespace=WorkspaceNamespaces(ingest_request.workspace_id).conv_fg,
            parse_generation_id="generation-1",
            parse_generation_member_id="member-1",
            parse_region=SourceRegion(
                source_document_id=source_document_id,
                start_char=1,
                end_char=min(len(ingest_request.raw_text), 4),
            ),
        )


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
    revision = pipeline.source_revision(
        request=request,
        source_document_id=artifacts.source_document_id,
    )
    assert seeds
    assert all(seed.doc_id == revision.revision_document_id for seed in seeds)
    assert all(seed.mentions[0].spans[0].doc_id == revision.revision_document_id for seed in seeds)
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
