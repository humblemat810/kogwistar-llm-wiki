"""Run a small, synthetic known-answer evaluation of the maintenance critic."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kogwistar_llm_wiki.maintenance.maintenance_observation import (
    MaintenanceObservationFrame,
    ObservationSubject,
    SubjectKind,
)
from kogwistar_llm_wiki.maintenance.observation_critic import (
    build_observation_critic,
    safe_observation_critic_error_code,
)

if __package__:
    from .profile_maintenance_e2e import _local_critic_provider_settings
else:
    from profile_maintenance_e2e import _local_critic_provider_settings


@dataclass(frozen=True)
class CriticQualityCase:
    case_id: str
    frame: MaintenanceObservationFrame
    expected_verdicts: frozenset[str]
    required_evidence_id: str | None


def _frame(
    *,
    frame_id: str,
    subject_id: str,
    kind: SubjectKind,
    label: str,
    summary: str,
    source_id: str,
    source_excerpt: str,
    relation_context: Sequence[Mapping[str, object]] = (),
    neighborhood_context: Sequence[Mapping[str, object]] = (),
) -> MaintenanceObservationFrame:
    workspace_id = "maintenance-critic-fixtures"
    namespace = f"ws:{workspace_id}:g:curated_kg"
    graph_subject = {"id": subject_id, "label": label, "summary": summary}
    relations = [dict(item) for item in relation_context]
    neighbors = [dict(item) for item in neighborhood_context]
    if kind in {"edge", "hyperedge"}:
        subject_relation = next(
            (record for record in relations if record.get("id") == subject_id),
            None,
        )
        if subject_relation is None:
            relations.insert(0, graph_subject)
        else:
            subject_relation.update(graph_subject)
    else:
        neighbors.insert(0, graph_subject)
    return MaintenanceObservationFrame(
        frame_id=frame_id,
        subject=ObservationSubject(
            kind=kind,
            subject_id=subject_id,
            workspace_id=workspace_id,
            namespace=namespace,
            source_document_id=f"source:{frame_id}",
            revision_id="rev-1",
            revision_document_id=f"revision:{frame_id}:1",
            source_digest=f"sha256:{frame_id}",
        ),
        source_context=(
            {
                "id": source_id,
                "evidence_role": "authoritative_source",
                "workspace_id": workspace_id,
                "namespace": f"ws:{workspace_id}:source",
                "source_revision_id": "rev-1",
                "excerpt": source_excerpt,
            },
        ),
        relation_context=tuple(relations),
        neighborhood_context=tuple(neighbors),
        parent_context=(),
        omitted_counts={},
        token_budget=4_000,
        source_evidence_required=True,
    )


def known_answer_cases() -> tuple[CriticQualityCase, ...]:
    """Use short invented evidence so the evaluation contains no user data."""
    clean_source = "The NVIDIA H100 Tensor Core GPU is an accelerator for data-center workloads."
    weak_label_source = (
        "The AMD Instinct MI300X is a data-center accelerator designed for AI and HPC workloads."
    )
    granularity_source = (
        "The GeForce RTX 5090 is a graphics processor. CUDA is a separate parallel-computing "
        "platform and programming model."
    )
    relation_source = (
        "CUDA is a parallel-computing platform. A GPU is a processor; this source does not state "
        "that CUDA causes the GPU to exist."
    )
    return (
        CriticQualityCase(
            case_id="clean-grounded-node",
            frame=_frame(
                frame_id="fixture-clean-v2",
                subject_id="node:h100",
                kind="node",
                label="NVIDIA H100 Tensor Core GPU",
                summary="An accelerator for data-center workloads.",
                source_id="evidence:clean-h100",
                source_excerpt=clean_source,
            ),
            expected_verdicts=frozenset(),
            required_evidence_id=None,
        ),
        CriticQualityCase(
            case_id="weak-label-node",
            frame=_frame(
                frame_id="fixture-weak-label-v2",
                subject_id="node:mi300x",
                kind="node",
                label="Device",
                summary="It is used to do computation.",
                source_id="evidence:weak-label-mi300x",
                source_excerpt=weak_label_source,
            ),
            expected_verdicts=frozenset({"weak_label"}),
            required_evidence_id="evidence:weak-label-mi300x",
        ),
        CriticQualityCase(
            case_id="conflated-concepts-node",
            frame=_frame(
                frame_id="fixture-conflated-v2",
                subject_id="node:gpu-and-cuda",
                kind="node",
                label="GeForce RTX 5090 and CUDA",
                summary="One chip that combines the GeForce RTX 5090 GPU and the CUDA platform.",
                source_id="evidence:conflated-gpu-cuda",
                source_excerpt=granularity_source,
            ),
            expected_verdicts=frozenset(
                {"misgrouped", "too_coarse", "contradictory"}
            ),
            required_evidence_id="evidence:conflated-gpu-cuda",
        ),
        CriticQualityCase(
            case_id="unsupported-causal-edge",
            frame=_frame(
                frame_id="fixture-unsupported-edge-v2",
                subject_id="edge:cuda-causes-gpu",
                kind="edge",
                label="CUDA causes GPU",
                summary="A causal relationship from the CUDA platform to a GPU.",
                source_id="evidence:unsupported-cuda-gpu",
                source_excerpt=relation_source,
                relation_context=(
                    {
                        "id": "edge:cuda-causes-gpu",
                        "relation": "causes",
                        "source_ids": ["node:cuda"],
                        "target_ids": ["node:gpu"],
                        "workspace_id": "maintenance-critic-fixtures",
                        "namespace": "ws:maintenance-critic-fixtures:g:curated_kg",
                    },
                ),
                neighborhood_context=(
                    {
                        "id": "node:cuda",
                        "label": "CUDA",
                        "summary": "A parallel-computing platform.",
                    },
                    {
                        "id": "node:gpu",
                        "label": "GPU",
                        "summary": "A graphics processor.",
                    },
                ),
            ),
            expected_verdicts=frozenset(
                {"relation_unsupported", "relation_misplaced", "contradictory"}
            ),
            required_evidence_id="evidence:unsupported-cuda-gpu",
        ),
    )


def _finding_data(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        data = model_dump(mode="json")
        if isinstance(data, dict):
            return data
    raise TypeError("critic returned a finding with an unsupported representation")


def evaluate_case_result(
    *,
    expected_verdicts: frozenset[str],
    required_evidence_id: str | None,
    findings: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Score expected labels and source citation; do not grade prose style."""
    if not expected_verdicts:
        passed = not findings
        matched_count = 0
    else:
        matched = [
            finding
            for finding in findings
            if finding.get("verdict") in expected_verdicts
            and required_evidence_id
            in tuple(str(value) for value in finding.get("evidence_ids", ()))
        ]
        passed = bool(matched) and all(
            finding.get("verdict") in expected_verdicts for finding in findings
        )
        matched_count = len(matched)
    return {
        "passed": passed,
        "expected_verdicts": sorted(expected_verdicts),
        "required_source_evidence_cited": (
            not required_evidence_id
            or any(
                required_evidence_id
                in tuple(str(value) for value in finding.get("evidence_ids", ()))
                for finding in findings
            )
        ),
        "expected_findings_matched": matched_count,
        "unexpected_finding_count": sum(
            finding.get("verdict") not in expected_verdicts for finding in findings
        ),
        "findings": [dict(finding) for finding in findings],
    }


def run_evaluation(
    *,
    base_url: str,
    model_name: str,
    context_tokens: int,
    output_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    settings = _local_critic_provider_settings(
        provider="llama_cpp",
        model=model_name,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        context_tokens=context_tokens,
        output_tokens=output_tokens,
    )
    critic = build_observation_critic(settings)
    case_results: list[dict[str, object]] = []
    for case in known_answer_cases():
        started = time.perf_counter()
        try:
            result = critic(case.frame, None)
            findings = [
                _finding_data(value)
                for value in result.get("findings", [])
            ]
            scored = evaluate_case_result(
                expected_verdicts=case.expected_verdicts,
                required_evidence_id=case.required_evidence_id,
                findings=findings,
            )
            case_results.append(
                {
                    "case_id": case.case_id,
                    "status": "succeeded",
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
                    **scored,
                }
            )
        except Exception as exc:  # noqa: BLE001 - provider SDK failures are classified, not raised.
            case_results.append(
                {
                    "case_id": case.case_id,
                    "status": "failed",
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
                    "error_type": type(exc).__name__,
                    "error_code": safe_observation_critic_error_code(exc),
                    "passed": False,
                }
            )
    passed_count = sum(case_result.get("passed") is True for case_result in case_results)
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model_name,
        "runtime": "Prism llama.cpp OpenAI-compatible endpoint",
        "base_url": base_url,
        "context_tokens": context_tokens,
        "output_tokens": output_tokens,
        "case_count": len(case_results),
        "passed_count": passed_count,
        "all_cases_passed": passed_count == len(case_results),
        "results": case_results,
        "limitations": [
            "Synthetic English fixtures; not a representative user corpus.",
            "Verdict/evidence alignment is scored; human semantic review remains necessary.",
            "One model response per case; this does not measure repeatability.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8181")
    parser.add_argument("--model", default="Ternary-Bonsai-2-27B-PTQ1_0.gguf")
    parser.add_argument("--context-tokens", type=int, default=4096)
    parser.add_argument("--output-tokens", type=int, default=1024)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_evaluation(
        base_url=args.base_url,
        model_name=args.model,
        context_tokens=args.context_tokens,
        output_tokens=args.output_tokens,
        timeout_seconds=args.timeout_seconds,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["all_cases_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
