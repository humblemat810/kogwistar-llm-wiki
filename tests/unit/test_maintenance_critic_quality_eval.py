from __future__ import annotations

from scripts.evaluate_maintenance_critic_quality import (
    evaluate_case_result,
    known_answer_cases,
)


def test_known_answer_cases_include_graph_claim_and_authoritative_source() -> None:
    cases = known_answer_cases()

    assert [case.case_id for case in cases] == [
        "clean-grounded-node",
        "weak-label-node",
        "conflated-concepts-node",
        "unsupported-causal-edge",
    ]
    for case in cases:
        frame = case.frame
        subject_records = (
            frame.relation_context
            if frame.subject.kind in {"edge", "hyperedge"}
            else frame.neighborhood_context
        )
        assert any(record.get("id") == frame.subject.subject_id for record in subject_records)
        assert frame.source_context[0]["evidence_role"] == "authoritative_source"
        assert frame.frame_id.endswith("-v2")


def test_clean_fixture_summary_stays_within_source_claims() -> None:
    clean = known_answer_cases()[0].frame

    assert clean.neighborhood_context[0]["summary"] == (
        "An accelerator for data-center workloads."
    )
    assert "data-center workloads" in clean.source_context[0]["excerpt"]
    assert "AI and HPC" not in clean.neighborhood_context[0]["summary"]


def test_conflated_fixture_accepts_source_grounded_contradiction_verdict() -> None:
    conflated = known_answer_cases()[2]

    assert "contradictory" in conflated.expected_verdicts
    assert conflated.required_evidence_id == "evidence:conflated-gpu-cuda"


def test_clean_fixture_rejects_any_critic_findings() -> None:
    result = evaluate_case_result(
        expected_verdicts=frozenset(),
        required_evidence_id=None,
        findings=[],
    )
    false_positive = evaluate_case_result(
        expected_verdicts=frozenset(),
        required_evidence_id=None,
        findings=[{"verdict": "weak_label", "evidence_ids": ["source:1"]}],
    )

    assert result["passed"] is True
    assert false_positive["passed"] is False
    assert false_positive["unexpected_finding_count"] == 1


def test_defect_fixture_requires_expected_verdict_and_source_citation() -> None:
    expected = frozenset({"weak_label"})
    matching = evaluate_case_result(
        expected_verdicts=expected,
        required_evidence_id="evidence:weak-label",
        findings=[
            {
                "verdict": "weak_label",
                "evidence_ids": ["node:1", "evidence:weak-label"],
            }
        ],
    )
    missing_source = evaluate_case_result(
        expected_verdicts=expected,
        required_evidence_id="evidence:weak-label",
        findings=[{"verdict": "weak_label", "evidence_ids": ["node:1"]}],
    )
    wrong_verdict = evaluate_case_result(
        expected_verdicts=expected,
        required_evidence_id="evidence:weak-label",
        findings=[
            {
                "verdict": "too_fine",
                "evidence_ids": ["evidence:weak-label"],
            }
        ],
    )

    assert matching["passed"] is True
    assert missing_source["passed"] is False
    assert missing_source["required_source_evidence_cited"] is False
    assert wrong_verdict["passed"] is False
