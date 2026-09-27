from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.maintenance.maintenance_observation import (
    ObservationSubject,
    assess_observation_frame,
    build_observation_frame,
)
from kogwistar_llm_wiki.maintenance.maintenance_strategies import (
    build_default_maintenance_strategy_registry,
)
from kogwistar_llm_wiki.maintenance.worker_execution import MaintenanceExecutionWorkerMixin
from kogwistar_llm_wiki.maintenance.worker_observation import MaintenanceObservationWorkerMixin
from kogwistar_llm_wiki.maintenance.worker_parse import (
    select_affected_crosslink_ids,
    select_affected_parse_view_artifact_ids,
)
from kogwistar_llm_wiki.parsing.parse_views import ParseView, ParseViewSelection, SourceRegion


def test_parse_view_switch_requeues_only_affected_local_crosslinks() -> None:
    edges = [
        SimpleNamespace(
            id="edge-local-left",
            metadata={
                "workspace_id": "demo",
                "crosslink_status": "accepted",
                "left_source_document_id": "source-a",
                "right_source_document_id": "source-b",
            },
        ),
        SimpleNamespace(
            id="edge-local-unrelated",
            metadata={
                "workspace_id": "demo",
                "crosslink_status": "accepted",
                "left_source_document_id": "source-c",
                "right_source_document_id": "source-d",
            },
        ),
        SimpleNamespace(
            id="edge-foreign",
            metadata={
                "workspace_id": "other",
                "crosslink_status": "accepted",
                "left_source_document_id": "source-a",
                "right_source_document_id": "source-z",
            },
        ),
        SimpleNamespace(
            id="edge-source-native",
            metadata={
                "workspace_id": "demo",
                "crosslink_status": "accepted",
                "source_document_id": "source-a",
                "source_native": True,
            },
        ),
        SimpleNamespace(
            id="edge-not-accepted",
            metadata={
                "workspace_id": "demo",
                "crosslink_status": "candidate",
                "left_source_document_id": "source-a",
                "right_source_document_id": "source-b",
            },
        ),
    ]

    assert select_affected_crosslink_ids(
        edges,
        workspace_id="demo",
        source_document_id="source-a",
    ) == ("edge-local-left",)


def test_crosslink_authority_rejects_endpoint_outside_security_scope(monkeypatch) -> None:
    node = SimpleNamespace(
        id="node-a",
        metadata={"workspace_id": "demo", "security_scope": "tenant-a"},
    )
    worker = SimpleNamespace(
        engines=SimpleNamespace(
            kg=SimpleNamespace(
                read=SimpleNamespace(
                    get_nodes=lambda **kwargs: [node],
                )
            )
        )
    )
    patch = SimpleNamespace(
        scope=SimpleNamespace(workspace_id="demo"),
        operations=[
            SimpleNamespace(from_node_id="node-a", to_node_id=None, provenance=None)
        ],
    )
    monkeypatch.setattr(
        "kogwistar_llm_wiki.maintenance.worker_execution.can_access_security_scope",
        lambda scope: False,
    )

    with pytest.raises(PermissionError, match="security scope"):
        MaintenanceExecutionWorkerMixin._validate_crosslink_authority(
            worker,
            SimpleNamespace(workspace_id="demo"),
            patch,
        )


def test_observation_reparse_target_rejects_mismatched_active_view(monkeypatch) -> None:
    view = ParseView(
        view_id="view-1",
        view_version=1,
        workspace_id="demo",
        source_document_id="source-1",
        source_revision_id="revision-current",
        revision_document_id="revision-doc-current",
        selections=(
            ParseViewSelection(
                member_id="member-1",
                generation_id="generation-1",
                region=SourceRegion(
                    source_document_id="revision-doc-current",
                    start_char=0,
                    end_char=10,
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        "kogwistar_llm_wiki.maintenance.worker_observation.ParseViewStore.get",
        lambda self, source_document_id: view,
    )
    worker = MaintenanceObservationWorkerMixin()
    worker.engines = SimpleNamespace(conversation=SimpleNamespace(meta_sqlite=object()))
    ctx = SimpleNamespace(
        workspace_id="demo",
        payload={
            "source_document_id": "source-1",
            "source_revision_id": "revision-stale",
            "revision_document_id": "revision-doc-stale",
            "parse_generation_member_id": "member-1",
        },
    )

    assert worker._derive_observation_parse_target(ctx) is None


def test_parse_view_switch_selection_is_bounded() -> None:
    edges = [
        SimpleNamespace(
            id=f"edge-{index}",
            metadata={
                "workspace_id": "demo",
                "crosslink_status": "stale",
                "source_document_id": "source-a",
            },
        )
        for index in range(5)
    ]

    assert select_affected_crosslink_ids(
        edges,
        workspace_id="demo",
        source_document_id="source-a",
        limit=2,
    ) == ("edge-0", "edge-1")


def test_parse_view_switch_selects_only_local_derived_summaries_and_projections() -> None:
    items = [
        SimpleNamespace(
            id="summary-local",
            metadata={
                "workspace_id": "demo",
                "artifact_kind": "derived_summary",
                "source_document_id": "source-a",
            },
        ),
        SimpleNamespace(
            id="projection-local",
            metadata={
                "workspace_id": "demo",
                "artifact_kind": "promoted_knowledge",
                "source_document_id": "source-a",
            },
        ),
        SimpleNamespace(
            id="summary-foreign",
            metadata={
                "workspace_id": "other",
                "artifact_kind": "derived_summary",
                "source_document_id": "source-a",
            },
        ),
    ]

    assert select_affected_parse_view_artifact_ids(
        items,
        workspace_id="demo",
        source_document_id="source-a",
        artifact_kinds={"derived_summary", "promoted_knowledge"},
    ) == ("projection-local", "summary-local")


def test_observation_frame_is_deterministic_and_bounded() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id="demo",
        namespace="ws:demo:curated",
    )
    frame = build_observation_frame(
        subject,
        source_context=[{"member_id": "member-1", "quality_status": "stable"}],
        relation_context=[{"relation_id": "edge-1", "evidence_ids": ["doc-1"]}],
        neighborhood_context=[{"node_id": f"node-{i}"} for i in range(100)],
        token_budget=256,
    )
    repeat = build_observation_frame(
        subject,
        source_context=[{"member_id": "member-1", "quality_status": "stable"}],
        relation_context=[{"relation_id": "edge-1", "evidence_ids": ["doc-1"]}],
        neighborhood_context=[{"node_id": f"node-{i}"} for i in range(100)],
        token_budget=256,
    )

    assert frame.frame_id == repeat.frame_id
    assert frame.omitted_counts["neighborhood"] > 0
    assert len(frame.neighborhood_context) < 100


def test_observation_frame_preserves_only_scoped_relation_evidence() -> None:
    subject = ObservationSubject(
        kind="edge",
        subject_id="edge-1",
        workspace_id="demo",
        namespace="ws:demo:g:curated_kg",
    )
    frame = build_observation_frame(
        subject,
        relation_context=[
            {"relation_id": "edge-other", "workspace_id": "other", "evidence_ids": ["doc-1"]},
            {"relation_id": "edge-unauthorized", "acl_authorized": False, "evidence_ids": ["doc-2"]},
            {"relation_id": "edge-local"},
        ],
    )

    assert [item["relation_id"] for item in frame.relation_context] == ["edge-local"]
    assessment = assess_observation_frame(frame)
    assert assessment.verdict == "review_required"
    assert assessment.recommended_action == "review_parent"
    assert assessment.continuation_allowed is True


def test_observation_frame_filters_revision_and_embedding_profile_mismatches() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id="demo",
        namespace="ws:demo:curated",
        revision_id="revision-2",
        revision_document_id="revision-doc-2",
        embedding_profile_fingerprint="profile-2",
    )
    frame = build_observation_frame(
        subject,
        source_context=[
            {"member_id": "old", "source_revision_id": "revision-1"},
            {"member_id": "old-doc", "revision_document_id": "revision-doc-1"},
            {"member_id": "old-profile", "profile_fingerprint": "profile-1"},
            {
                "member_id": "current",
                "source_revision_id": "revision-2",
                "revision_document_id": "revision-doc-2",
                "profile_fingerprint": "profile-2",
            },
        ],
    )

    assert [item["member_id"] for item in frame.source_context] == ["current"]


def test_observation_watermark_changes_with_active_parse_view() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id="demo",
        namespace="ws:demo:g:curated_kg",
        revision_id="revision-1",
        parse_member_id="member-1",
    )
    first = assess_observation_frame(
        build_observation_frame(subject, active_view_id="view-1", active_view_version=1)
    )
    second = assess_observation_frame(
        build_observation_frame(subject, active_view_id="view-1", active_view_version=2)
    )

    assert first.watermark_key != second.watermark_key
    assert first.assessment_id != second.assessment_id


def test_uncertain_parse_never_becomes_adequate_or_auto_repaired() -> None:
    subject = ObservationSubject(
        kind="edge",
        subject_id="edge-1",
        workspace_id="demo",
        namespace="ws:demo:source",
    )
    frame = build_observation_frame(
        subject,
        source_context=[{"member_id": "member-1", "quality_status": "expanding"}],
    )

    assessment = assess_observation_frame(frame)

    assert assessment.verdict == "review_required"
    assert assessment.recommended_action == "review_parent"
    assert assessment.continuation_allowed is True

    failed = assess_observation_frame(frame, critic_failed=True)
    assert failed.verdict == "quality_unknown"
    assert failed.recommended_action == "request_human_review"
    assert failed.critic_status == "failed"
    assert failed.continuation_allowed is False


@pytest.mark.parametrize(
    ("quality_status", "expected_verdict", "expected_action"),
    [
        ("too_coarse", "too_coarse", "expand_children"),
        ("too_fine", "too_fine", "switch_to_boundary"),
    ],
)
def test_parser_granularity_findings_choose_different_bounded_actions(
    quality_status: str,
    expected_verdict: str,
    expected_action: str,
) -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id="demo",
        namespace="ws:demo:source",
    )
    assessment = assess_observation_frame(
        build_observation_frame(
            subject,
            source_context=[{"member_id": "member-1", "quality_status": quality_status}],
        )
    )

    assert assessment.verdict == expected_verdict
    assert assessment.recommended_action == expected_action


def test_observation_priority_is_order_independent_and_frame_identity_tracks_evidence() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id="demo",
        namespace="ws:demo:source",
    )
    first = build_observation_frame(
        subject,
        source_context=[
            {"member_id": "m1", "quality_status": "too_coarse"},
            {"member_id": "m2", "quality_status": "coverage_gap"},
        ],
    )
    second = build_observation_frame(
        subject,
        source_context=list(reversed(first.source_context)),
    )
    assert first.frame_id == second.frame_id
    assert assess_observation_frame(first).recommended_action == "reparse_region"

    changed = build_observation_frame(
        subject,
        source_context=[{"member_id": "m1", "quality_status": "too_coarse"}],
    )
    assert changed.frame_id != first.frame_id


def test_relation_without_evidence_is_not_accepted_automatically() -> None:
    subject = ObservationSubject(
        kind="hyperedge",
        subject_id="hyperedge-1",
        workspace_id="demo",
        namespace="ws:demo:curated",
    )
    frame = build_observation_frame(subject, relation_context=[{"relation_id": "h-1"}])

    assessment = assess_observation_frame(frame)

    assert assessment.verdict == "review_required"
    assert any(item.verdict == "relation_unsupported" for item in assessment.findings)


def test_observation_strategy_is_registered_before_fallback() -> None:
    strategy = build_default_maintenance_strategy_registry().resolve("review_maintenance_subject")

    assert strategy.name == "maintenance_observation"


def test_observation_continuation_requeues_same_job_once_with_bounded_context() -> None:
    class Jobs:
        def __init__(self) -> None:
            self.payload = None

        def requeue_at_tail(self, _job, *, payload):
            self.payload = payload

    jobs = Jobs()
    worker = SimpleNamespace(
        engines=SimpleNamespace(conversation=SimpleNamespace(jobs=jobs)),
        _emit_trace=lambda *_args, **_kwargs: None,
    )
    ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        job=object(),
        maintenance_kind="review_maintenance_subject",
        payload={
            "subject_id": "ws:demo:child",
            "parent_subject_id": "ws:demo:parent",
            "maintenance_round": 0,
            "maintenance_max_rounds": 2,
        },
    )

    assert MaintenanceObservationWorkerMixin._schedule_observation_continuation(
        worker, ctx, "review_parent", True
    )
    assert jobs.payload["maintenance_kind"] == "review_maintenance_subject"
    assert jobs.payload["subject_id"] == "ws:demo:parent"
    assert jobs.payload["maintenance_round"] == 1
    assert jobs.payload["observation_continuation_scheduled"] is True

    assert not MaintenanceObservationWorkerMixin._schedule_observation_continuation(
        worker,
        SimpleNamespace(**{**ctx.__dict__, "payload": jobs.payload}),
        "review_parent",
        True,
    )


def test_crosslink_candidate_requires_two_source_documents() -> None:
    ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        maintenance_kind="document_propose_crosslinks",
        payload={
            "crosslink_candidate": {
                "left_node_id": "ws:demo:left",
                "right_node_id": "ws:demo:right",
                "relation": "related_to",
                "left_source_document_id": "doc-left",
                "right_source_document_id": "doc-left",
                "confidence": 0.7,
            }
        },
    )

    with pytest.raises(ValueError, match="two source documents"):
        MaintenanceExecutionWorkerMixin._build_crosslink_candidate_patch(object(), ctx)


def test_crosslink_candidate_patch_is_derived_and_not_accepted() -> None:
    ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        maintenance_kind="document_propose_crosslinks",
        payload={
            "crosslink_candidate": {
                "left_node_id": "ws:demo:left",
                "right_node_id": "ws:demo:right",
                "relation": "related_to",
                "left_source_document_id": "doc-left",
                "right_source_document_id": "doc-right",
                "confidence": 0.7,
                "source_pointers": [
                    {"doc_id": "doc-left", "start_char": 0, "end_char": 5},
                    {"doc_id": "doc-right", "start_char": 0, "end_char": 5},
                ],
            }
        },
    )

    patch = MaintenanceExecutionWorkerMixin._build_crosslink_candidate_patch(object(), ctx)

    assert patch.intent.value == "derive_crosslink_candidate"
    assert patch.operations[0].properties["crosslink_status"] == "candidate"
    assert patch.operations[0].provenance is not None
    assert len(patch.operations[0].provenance.source_pointers) == 2


def test_crosslink_acceptance_fails_closed_for_uncertain_or_unauthorized_context() -> None:
    ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        maintenance_kind="document_validate_crosslinks",
        payload={
            "accepted_confidence": 0.95,
            "parse_quality_status": "quality_unknown",
            "crosslink_acl_authorized": True,
        },
    )
    candidate_ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        maintenance_kind="document_propose_crosslinks",
        payload={
            "crosslink_candidate": {
                "left_node_id": "ws:demo:left",
                "right_node_id": "ws:demo:right",
                "relation": "related_to",
                "left_source_document_id": "doc-left",
                "right_source_document_id": "doc-right",
                "confidence": 0.7,
                "source_pointers": [
                    {"doc_id": "doc-left", "start_char": 0, "end_char": 5},
                    {"doc_id": "doc-right", "start_char": 0, "end_char": 5},
                ],
            }
        },
    )
    patch = MaintenanceExecutionWorkerMixin._build_crosslink_candidate_patch(object(), candidate_ctx)

    with pytest.raises(ValueError, match="quality_unknown"):
        MaintenanceExecutionWorkerMixin._promote_crosslink_candidate(ctx, patch)


def test_crosslink_revalidation_requires_a_derived_edge_and_preserves_old_until_acceptance() -> None:
    ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        maintenance_kind="document_revalidate_crosslinks",
        payload={
            "crosslink_candidate": {
                "left_node_id": "ws:demo:left",
                "right_node_id": "ws:demo:right",
                "relation": "related_to",
                "left_source_document_id": "doc-left",
                "right_source_document_id": "doc-right",
                "supersedes_edge_id": "ws:demo:edge:old",
                "superseded_edge_status": "stale",
                "confidence": 0.7,
                "source_pointers": [
                    {"doc_id": "doc-left", "start_char": 0, "end_char": 5},
                    {"doc_id": "doc-right", "start_char": 0, "end_char": 5},
                ],
            }
        },
    )

    patch = MaintenanceExecutionWorkerMixin._build_crosslink_candidate_patch(object(), ctx)

    assert patch.operations[0].properties["crosslink_status"] == "candidate"
    assert patch.operations[0].properties["supersedes_edge_id"] == "ws:demo:edge:old"
    assert not any(operation.kind.value == "TOMBSTONE_EDGE" for operation in patch.operations)

    accepted_ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        maintenance_kind="document_validate_crosslinks",
        payload={
            "accepted_confidence": 0.95,
            "crosslink_acl_authorized": True,
            "crosslink_scope_valid": True,
            "crosslink_profile_compatible": True,
            "crosslink_source_revision_current": True,
        },
    )
    accepted = MaintenanceExecutionWorkerMixin._promote_crosslink_candidate(accepted_ctx, patch)
    assert accepted.requires_atomic_replacement is True
    assert accepted.operations[0].supersedes_ids == ["ws:demo:edge:old"]
    assert accepted.operations[1].kind.value == "TOMBSTONE_EDGE"


def test_crosslink_revalidation_rejects_source_native_replacement() -> None:
    ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        maintenance_kind="document_revalidate_crosslinks",
        payload={
            "crosslink_candidate": {
                "left_node_id": "ws:demo:left",
                "right_node_id": "ws:demo:right",
                "relation": "related_to",
                "left_source_document_id": "doc-left",
                "right_source_document_id": "doc-right",
                "supersedes_edge_id": "ws:demo:edge:raw",
                "superseded_edge_status": "accepted",
                "superseded_edge_source_native": True,
                "source_pointers": [
                    {"doc_id": "doc-left", "start_char": 0, "end_char": 5},
                    {"doc_id": "doc-right", "start_char": 0, "end_char": 5},
                ],
            }
        },
    )

    with pytest.raises(ValueError, match="source-native"):
        MaintenanceExecutionWorkerMixin._build_crosslink_candidate_patch(object(), ctx)
