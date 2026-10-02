from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from kg_doc_parser.workflow_ingest.semantics import HydratedTextPointer, SemanticNode

from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest
from kogwistar_llm_wiki.maintenance.crosslink_proposals import (
    CrosslinkCriticResponse,
    CrosslinkEvidence,
)
from kogwistar_llm_wiki.maintenance.maintenance_strategies import (
    MaintenanceJobExecutionContext,
)
from kogwistar_llm_wiki.maintenance.worker_execution import (
    MaintenanceExecutionWorkerMixin,
)

pytestmark = pytest.mark.ci
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "crosslink_comparison"


def _trivial_tree(document_id: str) -> SemanticNode:
    text = "NVIDIA builds CUDA.\n\nThe training service uses CUDA kernels."
    return SemanticNode(
        node_id="root",
        title="CUDA relationship",
        total_content_pointers=[
            HydratedTextPointer(
                source_cluster_id=document_id,
                start_char=0,
                end_char=len(text),
                verbatim_text=text,
            )
        ],
        child_nodes=[
            SemanticNode(
                node_id="cuda-platform",
                parent_id="root",
                title="CUDA platform",
                total_content_pointers=[
                    HydratedTextPointer(
                        source_cluster_id=document_id,
                        start_char=0,
                        end_char=19,
                        verbatim_text="NVIDIA builds CUDA.",
                    )
                ],
                level_from_root=1,
            ),
            SemanticNode(
                node_id="cuda-use",
                parent_id="root",
                title="CUDA kernel use",
                total_content_pointers=[
                    HydratedTextPointer(
                        source_cluster_id=document_id,
                        start_char=21,
                        end_char=len(text),
                        verbatim_text="The training service uses CUDA kernels.",
                    )
                ],
                level_from_root=1,
            ),
        ],
    )


def _proposal_worker(evidence: list[CrosslinkEvidence]):
    class Worker(MaintenanceExecutionWorkerMixin):
        pass

    worker = object.__new__(Worker)
    traces: list[dict[str, object]] = []
    reviews: list[dict[str, object]] = []
    finished: list[dict[str, object]] = []
    provider_calls: list[list[dict[str, object]]] = []
    critic_calls: list[list[dict[str, object]]] = []
    validation_failures: list[str] = []
    mutations: list[str] = []
    def evidence_id(item: object) -> str:
        if hasattr(item, "evidence_id"):
            return str(item.evidence_id)
        return str(item["evidence_id"])

    worker._collect_crosslink_evidence = lambda _ctx: evidence
    def invoke_proposer(items, _ctx):
        provider_calls.append([item.model_dump(mode="json") for item in items])
        return {
            "groups": [
                {
                    "group_id": "cuda-link",
                    "rationale": "The two parsed pieces describe CUDA and its use.",
                    "operations": [
                        {
                            "left_evidence_id": evidence_id(items[0]),
                            "right_evidence_id": evidence_id(items[1]),
                            "relation": "uses",
                            "rationale": "The service uses CUDA kernels.",
                        }
                    ],
                }
            ]
        }

    def invoke_critic(_group, group_evidence, _ctx):
        critic_calls.append(group_evidence)
        return CrosslinkCriticResponse(
            verdict="approve",
            explanation="The excerpts support the relation.",
            evidence_ids=tuple(item["evidence_id"] for item in group_evidence),
        )

    def validate_authority(*_args):
        return None

    worker._invoke_crosslink_proposer = invoke_proposer
    worker._invoke_crosslink_critic = invoke_critic
    worker._validate_crosslink_authority = validate_authority
    worker._persist_crosslink_group_review = (
        lambda _ctx, group_id, _patch, _group, _review, *, status: reviews.append(
            {"group_id": group_id, "status": status}
        )
        or f"review:{group_id}"
    )
    worker._enqueue_crosslink_group_apply = (
        lambda _ctx, group_id, _patch: mutations.append(group_id)
    )
    worker._finish_crosslink_proposal = lambda _ctx, **result: finished.append(result)
    worker._emit_trace = lambda event, **fields: traces.append({"event": event, **fields})
    worker._trace_crosslink_workflow_stage = (
        lambda _ctx, stage, outcome, **fields: traces.append(
            {"event": "stage", "stage": stage, "outcome": outcome, **fields}
        )
    )
    worker._claim_lost = SimpleNamespace(is_set=lambda: False)
    worker.provider_settings = None
    ctx = MaintenanceJobExecutionContext(
        workspace_id="crosslink-comparison",
        job=None,
        job_id="comparison-job",
        payload={
            "parse_quality_status": "parsed",
            "budgets": {"max_llm_calls": 2, "max_tokens": 2000, "max_steps": 10},
        },
        request_node=None,
        request_node_id="comparison-request",
        lane_message_id="",
        maintenance_kind="document_propose_crosslinks",
    )
    return (
        worker,
        ctx,
        traces,
        reviews,
        finished,
        provider_calls,
        critic_calls,
        validation_failures,
        mutations,
    )


def _run_proposal(evidence: list[CrosslinkEvidence]) -> dict[str, object]:
    (
        worker,
        ctx,
        traces,
        reviews,
        finished,
        provider_calls,
        critic_calls,
        validation_failures,
        mutations,
    ) = _proposal_worker(evidence)
    worker._propose_background_crosslink_groups(ctx)
    return {
        "traces": traces,
        "reviews": reviews,
        "finished": finished,
        "provider_calls": provider_calls,
        "critic_calls": critic_calls,
        "validation_failures": validation_failures,
        "mutations": mutations,
        "budgets": ctx.payload["budgets"],
    }


def test_trivial_crosslink_differs_before_and_after_parse_persistence(
    namespace_engines, tmp_path
) -> None:
    # Case A: an unparsed source has no parsed pieces and cannot propose a link.
    no_parse = _run_proposal([])
    assert no_parse["finished"] == [{"groups": 0, "status": "no_candidate"}]
    assert no_parse["provider_calls"] == []
    assert no_parse["critic_calls"] == []
    assert no_parse["mutations"] == []
    assert no_parse["validation_failures"] == []

    parser_stages: list[str] = []

    def parse_fixture(**kwargs):
        parser_stages.append("parsed")
        return SimpleNamespace(semantic_tree=_trivial_tree(kwargs["document_id"]))

    pipeline = IngestPipeline(
        namespace_engines,
        parser=parse_fixture,
        debug_run_dir=tmp_path / "parse-comparison",
    )
    request = IngestPipelineRequest(
        workspace_id="crosslink-comparison",
        source_uri="file:///fixtures/cuda-trivial.txt",
        title="CUDA trivial relationship",
        raw_text=(FIXTURE_ROOT / "cuda_relationship.md")
        .read_text(encoding="utf-8")
        .split("\n\n", 1)[1]
        .strip(),
        promotion_mode="pending",
    )
    artifacts = pipeline.run(request)
    assert artifacts.maintenance_job_id
    parser_stages.append("persisted")
    assert parser_stages == ["parsed", "persisted"]

    document_id = pipeline._source_document_id(request)
    parsed_evidence = [
        CrosslinkEvidence(
            evidence_id="parsed-cuda-platform",
            node_id="ws:crosslink-comparison:cuda-platform",
            source_document_id=document_id,
            source_revision_id=document_id,
            revision_document_id=document_id,
            source_digest="1" * 64,
            start_char=0,
            end_char=19,
            excerpt="NVIDIA builds CUDA.",
        ),
        CrosslinkEvidence(
            evidence_id="parsed-cuda-use",
            node_id="ws:crosslink-comparison:cuda-use",
            source_document_id=document_id,
            source_revision_id=document_id,
            revision_document_id=document_id,
            source_digest="1" * 64,
            start_char=21,
            end_char=60,
            excerpt="The training service uses CUDA kernels.",
        ),
    ]
    parsed = _run_proposal(parsed_evidence)

    assert len(parsed["reviews"]) == 1
    assert parsed["reviews"][0]["status"] == "ready"
    assert parsed["finished"][0]["groups"] == 1
    assert len(parsed["provider_calls"]) == 1
    assert [item["evidence_id"] for item in parsed["provider_calls"][0]] == [
        "parsed-cuda-platform",
        "parsed-cuda-use",
    ]
    assert all(
        item["node_id"].startswith("ws:crosslink-comparison:")
        for item in parsed["provider_calls"][0]
    )
    assert len(parsed["critic_calls"]) == 1
    assert parsed["critic_calls"][0][0]["evidence_id"] == "parsed-cuda-platform"
    assert parsed["critic_calls"][0][1]["evidence_id"] == "parsed-cuda-use"
    assert parsed["validation_failures"] == []
    assert parsed["mutations"] == [parsed["reviews"][0]["group_id"]]
    assert parsed["budgets"] == {
        "max_llm_calls": 2,
        "max_tokens": 2000,
        "max_steps": 10,
    }
    assert any(
        item.get("stage") == "propose" and item.get("outcome") == "groups_proposed"
        for item in parsed["traces"]
    )
