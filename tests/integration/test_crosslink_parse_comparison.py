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
    def evidence_id(item: object) -> str:
        if hasattr(item, "evidence_id"):
            return str(item.evidence_id)
        return str(item["evidence_id"])

    worker._collect_crosslink_evidence = lambda _ctx: evidence
    worker._invoke_crosslink_proposer = lambda items, _ctx: {
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
    worker._invoke_crosslink_critic = lambda *_args: CrosslinkCriticResponse(
        verdict="approve",
        explanation="The excerpts support the relation.",
        evidence_ids=(evidence[0].evidence_id, evidence[1].evidence_id),
    )
    worker._validate_crosslink_authority = lambda *_args: None
    worker._persist_crosslink_group_review = (
        lambda _ctx, group_id, _patch, _group, _review, *, status: reviews.append(
            {"group_id": group_id, "status": status}
        )
        or f"review:{group_id}"
    )
    worker._enqueue_crosslink_group_apply = lambda *_args: None
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
        payload={"parse_quality_status": "parsed"},
        request_node=None,
        request_node_id="comparison-request",
        lane_message_id="",
        maintenance_kind="document_propose_crosslinks",
    )
    return worker, ctx, traces, reviews, finished


def _run_proposal(evidence: list[CrosslinkEvidence]) -> dict[str, object]:
    worker, ctx, traces, reviews, finished = _proposal_worker(evidence)
    worker._propose_background_crosslink_groups(ctx)
    return {"traces": traces, "reviews": reviews, "finished": finished}


def test_trivial_crosslink_differs_before_and_after_parse_persistence(
    namespace_engines, tmp_path
) -> None:
    # Case A: an unparsed source has no parsed pieces and cannot propose a link.
    no_parse = _run_proposal([])
    assert no_parse["finished"] == [{"groups": 0, "status": "no_candidate"}]

    pipeline = IngestPipeline(
        namespace_engines,
        parser=lambda **kwargs: SimpleNamespace(
            semantic_tree=_trivial_tree(kwargs["document_id"])
        ),
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
    assert any(
        item.get("stage") == "propose" and item.get("outcome") == "groups_proposed"
        for item in parsed["traces"]
    )
