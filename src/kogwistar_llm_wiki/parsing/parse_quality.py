from __future__ import annotations

from collections import defaultdict


def basic_sense_eval_from_graph_payload(
    *,
    graph_payload: dict[str, object],
    diagnostics: dict[str, object],
) -> dict[str, object]:
    """Score basic structural quality for a parsed graph payload."""

    def _normalize_excerpt(text: object) -> str:
        return " ".join(str(text or "").split()).strip()

    nodes = list(graph_payload.get("nodes") or [])
    node_count = len(nodes)
    node_types: set[str] = set()
    excerpt_spans_by_cluster: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    cluster_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    max_depth = 0

    for node in nodes:
        metadata = dict(node.get("metadata") or {})
        node_type = str(metadata.get("semantic_node_type") or node.get("type") or "").strip()
        if node_type:
            node_types.add(node_type)
        level_from_root = metadata.get("level_from_root")
        if isinstance(level_from_root, int):
            max_depth = max(max_depth, level_from_root + 1)
        for mention in node.get("mentions") or []:
            for span in mention.get("spans") or []:
                excerpt = _normalize_excerpt(span.get("excerpt"))
                source_cluster_id = span.get("source_cluster_id")
                start_char = span.get("start_char")
                end_char = span.get("end_char")
                if source_cluster_id is None or not isinstance(start_char, int) or not isinstance(end_char, int):
                    continue
                if end_char <= start_char:
                    continue
                cluster_id = str(source_cluster_id)
                interval = (max(0, start_char), max(0, end_char))
                cluster_intervals[cluster_id].append(interval)
                if excerpt and excerpt != " ":
                    excerpt_spans_by_cluster[cluster_id][excerpt].append(interval)

    covered_total = 0
    source_total = 0
    for intervals in cluster_intervals.values():
        if not intervals:
            continue
        intervals.sort()
        merged: list[tuple[int, int]] = []
        cur_start, cur_end = intervals[0]
        for start_char, end_char in intervals[1:]:
            if start_char <= cur_end:
                cur_end = max(cur_end, end_char)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = start_char, end_char
        merged.append((cur_start, cur_end))
        covered_total += sum(end_char - start_char for start_char, end_char in merged)
        source_total += max(end_char for _, end_char in merged)

    coverage_ratio = covered_total / source_total if source_total else 0.0
    duplicate_excerpt_hits = 0
    for excerpt_groups in excerpt_spans_by_cluster.values():
        for intervals in excerpt_groups.values():
            if len(intervals) <= 1:
                continue
            intervals.sort()
            merged_span_count = 0
            cur_start, cur_end = intervals[0]
            for start_char, end_char in intervals[1:]:
                if start_char <= cur_end:
                    cur_end = max(cur_end, end_char)
                else:
                    merged_span_count += 1
                    cur_start, cur_end = start_char, end_char
            merged_span_count += 1
            duplicate_excerpt_hits += max(0, len(intervals) - merged_span_count)
    page_index_diag = dict(diagnostics.get("page_index") or {})
    assignment_mode = str(page_index_diag.get("assignment_mode") or diagnostics.get("assignment_mode") or "")
    fallback_used = bool(
        page_index_diag.get("fallback_reason")
        or diagnostics.get("fallback_reason")
        or assignment_mode == "deterministic_fallback"
        or page_index_diag.get("refine_excerpts_fallback")
    )

    score = (
        min(coverage_ratio, 1.0) * 45.0
        + min(max_depth, 8) / 8.0 * 20.0
        + min(len(node_types), 6) / 6.0 * 15.0
        + min(node_count, 20) / 20.0 * 10.0
        - min(duplicate_excerpt_hits, 5) * 7.0
        - (10.0 if fallback_used else 0.0)
    )
    score = max(0.0, min(100.0, round(score, 1)))
    if score >= 70.0 and coverage_ratio >= 0.45 and duplicate_excerpt_hits == 0:
        verdict = "good"
    elif score >= 40.0:
        verdict = "mixed"
    else:
        verdict = "weak"

    return {
        "basic_sense_score": score,
        "basic_sense_verdict": verdict,
        "coverage_ratio": round(coverage_ratio, 4),
        "max_depth": max_depth,
        "node_count": node_count,
        "node_type_diversity": len(node_types),
        "duplicate_excerpt_hits": duplicate_excerpt_hits,
        "fallback_used": fallback_used,
    }
