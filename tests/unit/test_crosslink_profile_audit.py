from __future__ import annotations

import importlib
import io
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


def _profile_module(monkeypatch: pytest.MonkeyPatch):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    return importlib.import_module("profile_crosslink_maintenance_e2e")


def test_edge_profile_audit_keeps_relation_and_spans_but_not_source_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_edge = _profile_module(monkeypatch)._edge_profile_audit

    edge = SimpleNamespace(
        id="ws:profile:edge:1",
        source_ids=["node-a"],
        target_ids=["node-b"],
        relation="used_by",
        metadata={"workspace_id": "profile"},
        properties={"crosslink_status": "accepted"},
        iter_span=lambda: iter(
            [
                SimpleNamespace(
                    doc_id="revision-doc",
                    start_char=8,
                    end_char=16,
                    excerpt="sensitive excerpt",
                ),
                SimpleNamespace(
                    doc_id="revision-doc",
                    start_char=0,
                    end_char=6,
                    excerpt="another excerpt",
                ),
            ]
        ),
    )

    audit = audit_edge(edge)

    assert audit["relation"] == "used_by"
    assert audit["crosslink_status"] == "accepted"
    assert audit["source_ids"] == ["node-a"]
    assert audit["target_ids"] == ["node-b"]
    assert audit["evidence_spans"] == [
        {
            "doc_id": "revision-doc",
            "start_char": 0,
            "end_char": 6,
            "span_chars": 6,
        },
        {
            "doc_id": "revision-doc",
            "start_char": 8,
            "end_char": 16,
            "span_chars": 8,
        },
    ]
    assert "excerpt" not in repr(audit)


def test_profile_sample_summary_reports_medians_verdicts_and_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize = _profile_module(monkeypatch)._summarize_profile_samples
    samples = [
        {
            "timings_seconds": {
                "proposal_and_review": 100.0,
                "approval_revalidation_apply": 8.0,
            },
            "result": {
                "accepted_crosslink_edges": 1,
                "group_review_summaries": [{"critic_verdict": "approve"}],
                "index_job_status_counts": {"DONE": 12},
                "semantic_self_retrieval": True,
                "expected_semantic_relation_matched": True,
                "acl_policy_audit": "verified",
            },
        },
        {
            "timings_seconds": {
                "proposal_and_review": 120.0,
                "approval_revalidation_apply": 10.0,
            },
            "result": {
                "accepted_crosslink_edges": 1,
                "group_review_summaries": [{"critic_verdict": "approve"}],
                "index_job_status_counts": {"DONE": 13},
                "semantic_self_retrieval": True,
                "expected_semantic_relation_matched": True,
                "acl_policy_audit": "verified",
            },
        },
    ]

    summary = summarize(samples)

    assert summary["sample_count"] == 2
    assert summary["accepted_crosslink_edges"] == 2
    assert summary["critic_verdict_counts"] == {"approve": 2}
    assert summary["all_index_jobs_terminal_without_failures"] is True
    assert summary["all_semantic_self_retrieval_passed"] is True
    assert summary["all_expected_semantic_relations_matched"] is True
    assert summary["all_acl_policy_audits_verified"] is True
    assert summary["timings_seconds"]["proposal_and_review"]["median"] == 110.0


def test_profile_runtime_label_does_not_claim_llm_for_fixture_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_label = _profile_module(monkeypatch)._provider_runtime_label

    assert "no LLM inference" in runtime_label("fixture-replay")
    assert "Bonsai2" in runtime_label("live")


def test_embedding_phase_delta_reports_calls_items_and_encode_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delta = _profile_module(monkeypatch)._embedding_metrics_delta

    assert delta(
        {"invocation_count": 2, "item_count": 3, "total_time_ms": 10.25},
        {"invocation_count": 5, "item_count": 8, "total_time_ms": 31.75},
    ) == {"invocation_count": 3, "item_count": 5, "total_time_ms": 21.5}


def test_embedding_phase_delta_rejects_non_monotonic_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delta = _profile_module(monkeypatch)._embedding_metrics_delta

    with pytest.raises(ValueError, match="monotonic"):
        delta(
            {"invocation_count": 2, "item_count": 2, "total_time_ms": 10},
            {"invocation_count": 1, "item_count": 3, "total_time_ms": 15},
        )


def test_embedding_cache_instrumentation_counts_hits_and_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _profile_module(monkeypatch)

    class FakeMemorizedFunction:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str], list[float]] = {}

        def check_call_in_cache(self, query: str, *, model_name: str) -> bool:
            return (query, model_name) in self.values

        def __call__(self, query: str, *, model_name: str) -> list[float]:
            return self.values.setdefault((query, model_name), [0.1, 0.2])

    class FakeCachedEmbed:
        def __init__(self) -> None:
            self.function = FakeMemorizedFunction()

        def __call__(self, query: str) -> list[float]:
            return self.function(query, model_name="e5-snapshot-pooling-v1")

        def check_call_in_cache(self, query: str) -> bool:
            return self.function.check_call_in_cache(
                query, model_name="e5-snapshot-pooling-v1"
            )

    engines = SimpleNamespace()
    for space in ("conversation", "workflow", "kg", "wisdom"):
        setattr(
            engines,
            space,
            SimpleNamespace(cached_embed=FakeCachedEmbed()),
        )

    cache = module._instrument_embedding_caches(engines)
    engine = engines.conversation
    assert engine.cached_embed("repeat") == [0.1, 0.2]
    assert engine.cached_embed("repeat") == [0.1, 0.2]
    assert cache["snapshot"]()["conversation"] == {
        "lookups": 2,
        "hits": 1,
        "misses": 1,
    }


def test_measured_profile_audit_records_duration_without_changing_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _profile_module(monkeypatch)
    timings: list[dict[str, object]] = []
    result = module._measured_profile_audit(
        timings,
        "fixture",
        lambda value: {"count": value},
        3,
    )

    assert result == {"count": 3}
    assert len(timings) == 1
    assert timings[0]["audit"] == "fixture"
    assert isinstance(timings[0]["elapsed_ms"], float)
    assert timings[0]["elapsed_ms"] >= 0


def test_conversation_artifact_summary_reports_counts_without_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _profile_module(monkeypatch)
    monkeypatch.setattr(
        module,
        "_temporary_namespace",
        lambda *_args, **_kwargs: nullcontext(),
    )
    engine = SimpleNamespace(
        backend=SimpleNamespace(
            node_get=lambda **kwargs: {
                "ids": ["node-a", "node-b"],
                "metadatas": [
                    {"artifact_kind": "lane_message"},
                    {"artifact_kind": "maintenance_review"},
                ],
            },
            edge_get=lambda **kwargs: {
                "ids": ["edge-a"],
                "metadatas": [{"artifact_kind": "lane_message_edge"}],
            },
        ),
        read=SimpleNamespace(
            get_nodes=lambda **_kwargs: pytest.fail("must not hydrate graph models"),
            get_edges=lambda **_kwargs: pytest.fail("must not hydrate graph models"),
        ),
    )

    summary = module._conversation_artifact_summary(engine, "maintenance-background")
    timings = summary.pop("diagnostic_timing_ms")
    assert isinstance(timings, dict)
    assert timings["node_read"] >= 0
    assert timings["edge_read"] >= 0
    assert summary == {
        "node_count": 2,
        "node_artifact_kinds": {"lane_message": 1, "maintenance_review": 1},
        "edge_count": 1,
        "edge_artifact_kinds": {"lane_message_edge": 1},
    }


def test_edge_acl_audit_requires_endpoint_grounding_and_span_policy_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_acl = _profile_module(monkeypatch)._edge_acl_matches_endpoint_policy
    policy = {
        "mode": "scope",
        "owner_id": "maintenance-worker",
        "security_scope": "research-team",
        "shared_with_principals": (),
        "shared_with_groups": (),
    }
    edge_id = "edge-1"
    records = {
        ("node", "node-a", None): policy,
        ("node", "node-b", None): policy,
        ("edge", edge_id, None): policy,
        ("grounding", edge_id, f"{edge_id}::mention::0"): policy,
        ("span", edge_id, f"{edge_id}::mention::0::span::0"): policy,
        ("span", edge_id, f"{edge_id}::mention::0::span::1"): policy,
    }

    def latest_record(*, grain, truth_graph, entity_id, target_item_id=None):
        assert truth_graph == "knowledge"
        value = records.get((grain, entity_id, target_item_id))
        return None if value is None else SimpleNamespace(**value)

    engine = SimpleNamespace(
        acl_enabled=True,
        kg_graph_type="knowledge",
        acl_graph=SimpleNamespace(latest_record=latest_record),
    )
    edge = SimpleNamespace(
        id=edge_id,
        source_ids=["node-a"],
        target_ids=["node-b"],
        iter_span=lambda: iter([object(), object()]),
    )

    assert audit_acl(engine, edge) is True

    records[("span", edge_id, f"{edge_id}::mention::0::span::1")] = {
        **policy,
        "security_scope": "other-team",
    }
    assert audit_acl(engine, edge) is False

    engine.acl_enabled = False
    assert audit_acl(engine, edge) is False


def test_semantic_relation_audit_checks_direction_and_expected_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matches = _profile_module(monkeypatch)._semantic_relation_matches
    expected = {
        "source_ids": ["workload"],
        "target_ids": ["cuda"],
        "relation": "uses",
    }

    assert matches(
        expected,
        expected_source_id="workload",
        expected_target_id="cuda",
        expected_relation="uses",
    ) is True
    assert matches(
        expected,
        expected_source_id="cuda",
        expected_target_id="workload",
        expected_relation="uses",
    ) is False
    assert matches(
        expected,
        expected_source_id="workload",
        expected_target_id="cuda",
        expected_relation="depends_on",
    ) is False


def test_profile_failure_code_prefers_provider_and_critic_causes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure_code = _profile_module(monkeypatch)._profile_failure_code

    assert (
        failure_code(
            pending_group_count=0,
            stage_outcomes=[],
            provider_errors=[{"error_type": "LengthFinishReasonError"}],
        )
        == "provider_output_limit"
    )
    assert (
        failure_code(
            pending_group_count=0,
            stage_outcomes=[{"stage": "critic", "outcome": "rejected"}],
            provider_errors=[],
        )
        == "critic_rejected"
    )
    assert (
        failure_code(
            pending_group_count=0,
            stage_outcomes=[{"stage": "critic", "outcome": "reject"}],
            provider_errors=[],
        )
        == "critic_rejected"
    )


def test_profile_reads_actual_prism_context_from_model_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _profile_module(monkeypatch)
    requested_urls: list[str] = []

    def fake_urlopen(url: str, *, timeout: int):
        requested_urls.append(url)
        assert timeout == 10
        return io.BytesIO(
            json.dumps(
                {"data": [{"meta": {"n_ctx": 32768}}, {"meta": {"n_ctx": 32768}}]}
            ).encode()
        )

    monkeypatch.setattr(module, "urlopen", fake_urlopen)

    assert module._served_context_tokens("http://127.0.0.1:8181/v1/") == 32768
    assert requested_urls == ["http://127.0.0.1:8181/v1/models"]


def test_profile_rejects_missing_or_ambiguous_prism_context_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _profile_module(monkeypatch)
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(
            json.dumps({"data": [{"id": "model-without-context"}]}).encode()
        ),
    )

    with pytest.raises(RuntimeError, match="unambiguous loaded meta.n_ctx"):
        module._served_context_tokens("http://127.0.0.1:8181")


def test_profile_summarizes_index_jobs_by_status_and_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _profile_module(monkeypatch)
    monkeypatch.setattr(module, "_temporary_namespace", lambda *_args: nullcontext())
    summarize_jobs = module._index_queue_summary
    requested: list[tuple[str, int]] = []

    class Meta:
        def list_index_jobs(self, *, namespace: str, limit: int):
            requested.append((namespace, limit))
            return [
                SimpleNamespace(
                    status="DONE",
                    entity_kind="node",
                    entity_id="node-a",
                    index_kind="doc",
                    op="UPSERT",
                ),
                SimpleNamespace(
                    status="PENDING",
                    entity_kind="edge",
                    entity_id="edge-a",
                    index_kind="doc",
                    op="UPSERT",
                ),
                SimpleNamespace(
                    status="DONE",
                    entity_kind="node",
                    entity_id="node-b",
                    index_kind="doc",
                    op="UPSERT",
                ),
            ]

        @staticmethod
        def iter_entity_events(*, namespace: str, from_seq: int):
            assert from_seq == 1
            if namespace != "conversation-bg":
                return iter([])
            return iter(
                [
                    (
                        1,
                        "node",
                        "node-a",
                        "ADD",
                        json.dumps({"metadata": {"artifact_kind": "lane_conversation"}}),
                    ),
                    (2, "edge", "edge-a", "DELETE", "{}"),
                ]
            )

    class Backend:
        @staticmethod
        def node_get(*, ids, include, limit):
            assert include == ["metadatas"]
            return {
                "ids": ["node-a"] if "node-a" in ids else [],
                "metadatas": (
                    [
                        {
                            "lifecycle_status": "active",
                            "security_scope": "private-team",
                        }
                    ]
                    if "node-a" in ids
                    else []
                ),
            }

        @staticmethod
        def edge_get(*, ids, include, limit):
            assert include == ["metadatas"]
            return {"ids": [], "metadatas": []}

    summary = summarize_jobs(
        SimpleNamespace(meta_sqlite=Meta(), backend=Backend()), "conversation-bg"
    )
    timings = summary.pop("diagnostic_timing_ms")
    assert isinstance(timings, dict)
    assert timings["job_listing"] >= 0
    assert timings["backend_entity_reads"] >= 0
    assert isinstance(timings["event_scan_by_namespace"], dict)
    assert timings["total"] >= 0
    assert summary == {
        "total": 3,
        "status_counts": {"DONE": 2, "PENDING": 1},
        "operation_counts": {"edge::doc::UPSERT": 1, "node::doc::UPSERT": 2},
        "distinct_entities_by_kind": {"edge": 1, "node": 2},
        "backend_entity_presence_by_kind": {
            "edge": {"missing": 1},
            "node": {"active": 1, "missing": 1},
        },
        "canonical_event_state_by_kind": {
            "edge": {"deleted": 1},
            "node": {"active": 1, "no_event": 1},
        },
        "canonical_event_state_by_namespace_candidate": {
            "requested": {
                "edge": {"deleted": 1},
                "node": {"active": 1, "no_event": 1},
            },
            "default": {
                "edge": {"no_event": 1},
                "node": {"no_event": 2},
            },
        },
        "active_event_missing_backend_by_kind": {"edge": 0, "node": 0},
        "active_event_missing_backend_by_namespace_candidate": {
            "requested": {"edge": 0, "node": 0},
            "default": {"edge": 0, "node": 0},
        },
        "distinct_projection_keys_by_kind": {"edge::doc": 1, "node::doc": 2},
        "duplicate_projection_job_rows": 0,
        "jobs_per_entity": {
            "edge": {"entity_count": 1, "median_job_rows": 1, "max_job_rows": 1},
            "node": {"entity_count": 2, "median_job_rows": 1.0, "max_job_rows": 1},
        },
    }
    assert "node-a" not in repr(summary)
    assert "private-team" not in repr(summary)
    assert requested == [("conversation-bg", 10_000)]


def test_known_answer_provider_replay_uses_fixture_evidence_and_expected_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _profile_module(monkeypatch)
    context = [
        {"node_id": "N2", "evidence_id": "ev-second"},
        {"node_id": "N1", "evidence_id": "ev-first"},
    ]

    proposal = module._known_answer_proposal(context, object())

    operation = proposal["groups"][0]["operations"][0]
    assert operation == {
        "left_evidence_id": "ev-first",
        "right_evidence_id": "ev-second",
        "relation": "uses",
        "rationale": "Replay fixture expected relation: uses.",
    }
    critic = module._known_answer_critic(
        {"evidence": context}, object()
    )
    assert critic["verdict"] == "approve"
    assert critic["evidence_ids"] == ["ev-second", "ev-first"]


def test_known_answer_provider_replay_fails_closed_without_two_evidence_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = _profile_module(monkeypatch)._known_answer_proposal

    assert proposal([{"node_id": "N1", "evidence_id": "ev-one"}], object()) == {
        "groups": []
    }


def test_profile_summary_fails_closed_for_missing_sample_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = _profile_module(monkeypatch)._summarize_profile_samples([{}])

    assert summary["failed_sample_count"] == 0
    assert summary["all_samples_passed"] is False
    assert summary["all_index_jobs_terminal_without_failures"] is False
    assert summary["all_semantic_self_retrieval_passed"] is False
    assert summary["all_expected_semantic_relations_matched"] is False
    assert summary["all_acl_policy_audits_verified"] is False


def test_profile_summary_marks_model_rejection_as_failed_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize = _profile_module(monkeypatch)._summarize_profile_samples

    summary = summarize(
        [
            {
                "status": "failed",
                "result": {
                    "status": "failed",
                    "failure_code": "provider_output_limit",
                    "provider_errors": [
                        {"error_type": "LengthFinishReasonError"},
                    ],
                    "failure_stage": "human_review_pending_persistence",
                    "group_review_summaries": [
                        {"critic_verdict": "reject"},
                    ],
                },
            }
        ]
    )

    assert summary["failed_sample_count"] == 1
    assert summary["all_samples_passed"] is False
    assert summary["critic_verdict_counts"] == {"reject": 1}
    assert summary["failure_code_counts"] == {"provider_output_limit": 1}
    assert summary["provider_error_type_counts"] == {
        "LengthFinishReasonError": 1,
    }
    assert summary["all_index_jobs_terminal_without_failures"] is False


def test_multisample_profile_records_failure_and_runs_remaining_samples(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _profile_module(monkeypatch)
    calls = 0
    acl_options: list[bool] = []

    def fake_run(
        *,
        dsn: str,
        base_url: str,
        model: str,
        output_tokens: int,
        output: Path | None,
        acl_enabled: bool = False,
        provider_mode: str = "live",
        embedding_device: str = "cuda",
        embedding_cache_dir: Path | None = None,
    ):
        nonlocal calls
        calls += 1
        acl_options.append(acl_enabled)
        assert provider_mode == "live"
        assert embedding_device == "cuda"
        assert embedding_cache_dir is None
        if calls == 1:
            if output is not None:
                output.write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "result": {
                                "status": "failed",
                                "failure_code": "critic_rejected",
                                "group_review_summaries": [
                                    {"critic_verdict": "reject"},
                                ],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            raise RuntimeError("profile gate failed")
        return {
            "status": "passed",
            "result": {
                "status": "passed",
                "accepted_crosslink_edges": 1,
                "group_review_summaries": [{"critic_verdict": "approve"}],
                "index_job_status_counts": {"DONE": 1},
                "semantic_self_retrieval": True,
                "expected_semantic_relation_matched": True,
                "acl_policy_audit": "verified",
            },
        }

    output = tmp_path / "profile.json"
    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile_crosslink_maintenance_e2e.py",
            "--dsn",
            "postgresql://profile.invalid/test",
            "--samples",
            "2",
            "--acl-enabled",
            "--output",
            str(output),
        ],
    )

    assert module.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert calls == 2
    assert acl_options == [True, True]
    assert report["summary"]["sample_count"] == 2
    assert report["summary"]["failed_sample_count"] == 1
    assert report["summary"]["all_samples_passed"] is False
    assert report["samples"][0]["result"]["failure_code"] == "critic_rejected"
    assert report["samples"][1]["status"] == "passed"
