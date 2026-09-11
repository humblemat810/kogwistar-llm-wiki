from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


def operation_category(maintenance_kind: str) -> str:
    """Map an application maintenance kind to a review-friendly category."""
    value = str(maintenance_kind or "unknown").lower()
    if "seed" in value:
        return "source_seeding"
    if "parse" in value or "extract" in value:
        return "breakdown"
    if any(token in value for token in ("link", "crosslink", "edge")):
        return "linking"
    if any(token in value for token in ("split", "breakdown", "decompose")):
        return "breakdown"
    if any(token in value for token in ("merge", "combine", "consolidate")):
        return "combining"
    if any(token in value for token in ("repair", "correct", "fix")):
        return "correction"
    if any(token in value for token in ("distill", "derive", "wisdom")):
        return "distillation"
    return value or "unknown"


def build_maintenance_statistics(
    trace_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Build a read-side maintenance report from append-only worker traces."""
    documents: dict[str, dict[str, Any]] = {}
    totals = Counter()
    failure_hotspots: Counter[str] = Counter()
    operation_totals: Counter[str] = Counter()

    def document(row: dict[str, Any]) -> dict[str, Any]:
        doc_id = str(row.get("source_document_id") or "unattributed")
        if doc_id not in documents:
            documents[doc_id] = {
                "source_document_id": doc_id,
                "attempt_count": 0,
                "completed_count": 0,
                "suspended_count": 0,
                "requeued_count": 0,
                "blocked_count": 0,
                "failed_count": 0,
                "duration_ms_total": 0,
                "duration_ms_max": 0,
                "operation_categories": {},
                "workers": [],
                "derived_node_count": 0,
                "source_node_count": 0,
                "parse_count": 0,
                "parse_duration_ms_total": 0,
                "parsed_node_count": 0,
                "parsed_edge_count": 0,
                "llm_call_count": 0,
                "failure_reasons": {},
            }
        return documents[doc_id]

    for row in trace_rows:
        event = str(row.get("event") or "")
        if not row.get("source_document_id") and event not in {
            "maintenance_job_start",
            "maintenance_runtime_attempt_start",
            "maintenance_runtime_attempt_complete",
            "maintenance_job_requeued",
            "maintenance_job_blocked",
            "maintenance_runtime_attempt_failed",
            "maintenance_graph_effect",
        }:
            continue
        item = document(row)
        worker_id = str(row.get("worker_id") or "")
        if worker_id and worker_id not in item["workers"]:
            item["workers"].append(worker_id)
        category = operation_category(str(row.get("maintenance_kind") or ""))
        operation_counts = item["operation_categories"]

        if event == "maintenance_runtime_attempt_start":
            operation_counts[category] = int(operation_counts.get(category, 0)) + 1
            operation_totals[category] += 1
            item["attempt_count"] += 1
            totals["attempt_count"] += 1
        elif event == "maintenance_runtime_attempt_complete":
            status = str(row.get("runtime_status") or "unknown")
            duration = int(row.get("duration_ms") or 0)
            item["duration_ms_total"] += duration
            item["duration_ms_max"] = max(item["duration_ms_max"], duration)
            item["completed_count"] += int(status in {"success", "succeeded", "completed", "finished"})
            item["suspended_count"] += int(status == "suspended")
            totals[f"status_{status}"] += 1
        elif event == "maintenance_job_requeued":
            item["requeued_count"] += 1
            totals["requeued_count"] += 1
        elif event == "maintenance_job_blocked":
            reason = str(row.get("reason") or "unknown")
            item["blocked_count"] += 1
            item["failure_reasons"][reason] = int(item["failure_reasons"].get(reason, 0)) + 1
            failure_hotspots[f"blocked:{reason}"] += 1
            totals["blocked_count"] += 1
        elif event == "maintenance_runtime_attempt_failed":
            operation_counts[category] = int(operation_counts.get(category, 0)) + 1
            operation_totals[category] += 1
            reason = str(row.get("error_type") or row.get("error") or "unknown")
            item["failed_count"] += 1
            item["failure_reasons"][reason] = int(item["failure_reasons"].get(reason, 0)) + 1
            failure_hotspots[f"failed:{reason}"] += 1
            totals["failed_count"] += 1
        elif event == "maintenance_graph_effect":
            operation_counts[category] = int(operation_counts.get(category, 0)) + 1
            operation_totals[category] += 1
            item["derived_node_count"] += int(row.get("derived_node_count") or 0)
            item["source_node_count"] += int(row.get("source_node_count") or 0)
            totals["derived_node_count"] += int(row.get("derived_node_count") or 0)
            totals["source_node_count"] += int(row.get("source_node_count") or 0)
        elif event == "maintenance_parse_complete":
            operation_counts[category] = int(operation_counts.get(category, 0)) + 1
            operation_totals[category] += 1
            item["parse_count"] += 1
            item["parse_duration_ms_total"] += int(row.get("duration_ms") or 0)
            item["parsed_node_count"] += int(row.get("node_count") or 0)
            item["parsed_edge_count"] += int(row.get("edge_count") or 0)
            item["llm_call_count"] += int(row.get("llm_call_count") or 0)
            totals["parse_count"] += 1
            totals["llm_call_count"] += int(row.get("llm_call_count") or 0)
        elif event == "maintenance_parse_failed":
            operation_counts[category] = int(operation_counts.get(category, 0)) + 1
            operation_totals[category] += 1
            reason = str(row.get("error_type") or row.get("error") or "unknown")
            item["failure_reasons"][reason] = int(item["failure_reasons"].get(reason, 0)) + 1
            failure_hotspots[f"parse_failed:{reason}"] += 1
            item["failed_count"] += 1
            totals["failed_count"] += 1

    for item in documents.values():
        item["workers"] = sorted(item["workers"])
        item["average_duration_ms"] = (
            round(item["duration_ms_total"] / item["attempt_count"], 2)
            if item["attempt_count"]
            else 0
        )
        item["operation_categories"] = dict(sorted(item["operation_categories"].items()))
        item["failure_reasons"] = dict(sorted(item["failure_reasons"].items()))

    return {
        "document_count": len(documents),
        "documents": dict(sorted(documents.items())),
        "operation_totals": dict(sorted(operation_totals.items())),
        "failure_hotspots": dict(failure_hotspots.most_common()),
        "totals": dict(totals),
    }
