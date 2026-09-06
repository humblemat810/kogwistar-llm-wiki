"""Compare persisted longrun parser runs on the same corpus.

This module intentionally reads diagnostic artifacts instead of querying a live
backend. A comparison is therefore reproducible after the runs finish and can
be reviewed without re-running the providers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _run_doc_rows(run_dir: Path) -> dict[str, dict[str, Any]]:
    dump_dir = run_dir / "dump"
    evaluation = _read_json(dump_dir / "progress_summary.json", {}).get("parser_eval", {})
    quality_rows = {
        str(row.get("doc_id")): dict(row)
        for row in evaluation.get("documents", [])
        if isinstance(row, dict) and row.get("doc_id")
    }
    usage_rows = {
        str(row.get("doc_id")): dict(row)
        for row in evaluation.get("usage_documents", [])
        if isinstance(row, dict) and row.get("doc_id")
    }
    manifest_rows = {
        str(row.get("doc_id")): dict(row)
        for row in _read_jsonl(dump_dir / "manifest.jsonl")
        if row.get("doc_id")
    }
    rows: dict[str, dict[str, Any]] = {}
    for doc_id in set(quality_rows) | set(usage_rows) | set(manifest_rows):
        row = {"doc_id": doc_id}
        row.update(manifest_rows.get(doc_id, {}))
        row.update(quality_rows.get(doc_id, {}))
        row.update(usage_rows.get(doc_id, {}))
        rows[doc_id] = row
    return rows


def _retry_and_boundary_metrics(run_dir: Path, doc_id: str) -> dict[str, int]:
    """Extract observable boundary/retry counts from the persisted layer log."""
    log = _read_json(run_dir / "dump" / "parser_layer_logs" / f"{doc_id}.json", [])
    proposal_retries = 0
    review_retries = 0
    proposed = accepted = rejected = shifted = unresolved = 0
    if not isinstance(log, list):
        return {
            "proposal_retries": 0,
            "review_retries": 0,
            "boundaries_proposed": 0,
            "boundaries_accepted": 0,
            "boundaries_rejected": 0,
            "boundaries_shifted": 0,
            "unresolved_intervals": 0,
        }
    for event in log:
        if not isinstance(event, dict):
            continue
        stage = str(event.get("stage", ""))
        retry_count = int(event.get("retry_count") or 0)
        if "proposal_result" in stage:
            proposal_retries = max(proposal_retries, retry_count)
            proposed += int(event.get("boundary_count") or 0)
            accepted += int(event.get("accepted_boundary_count") or 0)
            rejected += int(event.get("rejected_boundary_count") or 0)
            shifted += int(event.get("shifted_boundary_count") or 0)
            unresolved += int(event.get("unresolved_interval_count") or 0)
        elif "review_result" in stage or "review_completed" in stage:
            review_retries = max(review_retries, retry_count)
    return {
        "proposal_retries": proposal_retries,
        "review_retries": review_retries,
        "boundaries_proposed": proposed,
        "boundaries_accepted": accepted,
        "boundaries_rejected": rejected,
        "boundaries_shifted": shifted,
        "unresolved_intervals": unresolved,
    }


def load_parse_run(run_dir: str | Path) -> dict[str, Any]:
    """Load the stable comparison surface from one completed or partial run."""
    path = Path(run_dir).expanduser().resolve()
    config = _read_json(path / "dump" / "run_config.json", {})
    progress = _read_json(path / "dump" / "progress_summary.json", {})
    rows = _run_doc_rows(path)
    for doc_id, row in rows.items():
        row.update(_retry_and_boundary_metrics(path, doc_id))
    return {
        "run_dir": str(path),
        "model": config.get("parser_model") or config.get("ollama_model"),
        "provider": config.get("parser_provider"),
        "proposal_mode": config.get("parser_proposal_mode"),
        "corpus_fingerprint": config.get("corpus_fingerprint"),
        "doc_limit": config.get("doc_limit"),
        "completed_count": progress.get("completed_count"),
        "failed_count": progress.get("failed_count"),
        "documents": rows,
    }


def compare_parse_runs(left_dir: str | Path, right_dir: str | Path) -> dict[str, Any]:
    """Compare two persisted runs and return JSON-serializable metrics."""
    left = load_parse_run(left_dir)
    right = load_parse_run(right_dir)
    left_docs = left["documents"]
    right_docs = right["documents"]
    doc_ids = sorted(set(left_docs) | set(right_docs))
    rows: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        l = left_docs.get(doc_id, {})
        r = right_docs.get(doc_id, {})
        rows.append(
            {
                "doc_id": doc_id,
                "left": _comparison_metrics(l),
                "right": _comparison_metrics(r),
                "delta_right_minus_left": _numeric_delta(r, l),
            }
        )
    return {
        "same_corpus": bool(left.get("corpus_fingerprint") == right.get("corpus_fingerprint")),
        "corpus_fingerprint_left": left.get("corpus_fingerprint"),
        "corpus_fingerprint_right": right.get("corpus_fingerprint"),
        "left": {key: value for key, value in left.items() if key != "documents"},
        "right": {key: value for key, value in right.items() if key != "documents"},
        "documents": rows,
    }


def _comparison_metrics(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "status", "basic_sense_score", "basic_sense_verdict", "coverage_ratio",
            "fallback_used", "duplicate_excerpt_hits", "llm_call_count", "total_tokens",
            "total_cost", "time_ms", "proposal_retries", "review_retries",
            "boundaries_proposed", "boundaries_accepted", "boundaries_rejected",
            "boundaries_shifted", "unresolved_intervals",
        )
    }


def _numeric_delta(right: dict[str, Any], left: dict[str, Any]) -> dict[str, float]:
    keys = (
        "basic_sense_score", "coverage_ratio", "llm_call_count", "total_tokens",
        "total_cost", "time_ms", "proposal_retries", "review_retries",
        "boundaries_rejected", "boundaries_shifted", "unresolved_intervals",
    )
    result: dict[str, float] = {}
    for key in keys:
        try:
            if right.get(key) is not None and left.get(key) is not None:
                result[key] = float(right[key]) - float(left[key])
        except (TypeError, ValueError):
            continue
    return result


def render_parse_comparison_markdown(comparison: dict[str, Any]) -> str:
    """Render a compact human-review table from ``compare_parse_runs``."""
    left = comparison["left"]
    right = comparison["right"]
    lines = [
        "# Parser Run Comparison",
        "",
        f"- Same corpus: `{comparison['same_corpus']}`",
        f"- Left: `{left.get('model')}` ({left.get('run_dir')})",
        f"- Right: `{right.get('model')}` ({right.get('run_dir')})",
        "",
        "| Document | Model | Status | Score | Calls | Tokens | Cost USD | Time ms | Rejects | Retries |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison["documents"]:
        for side in ("left", "right"):
            metrics = row[side]
            lines.append(
                "| {doc} | {model} | {status} | {score} | {calls} | {tokens} | {cost} | {time} | {rejects} | {retries} |".format(
                    doc=row["doc_id"], model=(left if side == "left" else right).get("model"),
                    status=metrics.get("status"), score=metrics.get("basic_sense_score"),
                    calls=metrics.get("llm_call_count"), tokens=metrics.get("total_tokens"),
                    cost=metrics.get("total_cost"), time=metrics.get("time_ms"),
                    rejects=metrics.get("boundaries_rejected"),
                    retries=(metrics.get("proposal_retries", 0) or 0) + (metrics.get("review_retries", 0) or 0),
                )
            )
    return "\n".join(lines) + "\n"


def write_parse_comparison(
    left_dir: str | Path,
    right_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Persist ``comparison.json`` and ``comparison.md`` for later review."""
    comparison = compare_parse_runs(left_dir, right_dir)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "comparison.md").write_text(
        render_parse_comparison_markdown(comparison), encoding="utf-8"
    )
    return comparison
