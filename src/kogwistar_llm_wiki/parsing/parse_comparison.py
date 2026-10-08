"""Compare persisted longrun parser runs on the same corpus.

This module intentionally reads diagnostic artifacts instead of querying a live
backend. A comparison is therefore reproducible after the runs finish and can
be reviewed without re-running the providers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


def _objects(value: JsonValue | None) -> list[JsonObject]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _object_map(value: JsonValue | None) -> dict[str, JsonObject]:
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(value, dict) and isinstance(item, dict)
    } if isinstance(value, dict) else {}


def _int_value(value: JsonValue | None, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return default


def _float_value(value: JsonValue | None) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _read_json(path: Path, default: JsonValue) -> JsonValue:
    try:
        return cast(JsonValue, json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return default


def _as_object(value: JsonValue) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[JsonObject]:
    rows: list[JsonObject] = []
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
            rows.append(cast(JsonObject, value))
    return rows


def _run_doc_rows(run_dir: Path) -> dict[str, JsonObject]:
    dump_dir = run_dir / "dump"
    progress = _as_object(_read_json(dump_dir / "progress_summary.json", {}))
    evaluation = _as_object(progress.get("parser_eval", {}))
    quality_rows = {
        str(row.get("doc_id")): dict(row)
        for row in _objects(evaluation.get("documents"))
        if row.get("doc_id")
    }
    usage_rows = {
        str(row.get("doc_id")): dict(row)
        for row in _objects(evaluation.get("usage_documents"))
        if row.get("doc_id")
    }
    manifest_rows = {
        str(row.get("doc_id")): dict(row)
        for row in _read_jsonl(dump_dir / "manifest.jsonl")
        if row.get("doc_id")
    }
    rows: dict[str, JsonObject] = {}
    for doc_id in set(quality_rows) | set(usage_rows) | set(manifest_rows):
        row: JsonObject = {"doc_id": doc_id}
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
        retry_count = _int_value(event.get("retry_count"))
        if "proposal_result" in stage:
            proposal_retries = max(proposal_retries, retry_count)
            proposed += _int_value(event.get("boundary_count"))
            accepted += _int_value(event.get("accepted_boundary_count"))
            rejected += _int_value(event.get("rejected_boundary_count"))
            shifted += _int_value(event.get("shifted_boundary_count"))
            unresolved += _int_value(event.get("unresolved_interval_count"))
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


def load_parse_run(run_dir: str | Path) -> JsonObject:
    """Load the stable comparison surface from one completed or partial run."""
    path = Path(run_dir).expanduser().resolve()
    config = _as_object(_read_json(path / "dump" / "run_config.json", {}))
    progress = _as_object(_read_json(path / "dump" / "progress_summary.json", {}))
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
        "documents": cast(JsonValue, rows),
    }


def compare_parse_runs(left_dir: str | Path, right_dir: str | Path) -> JsonObject:
    """Compare two persisted runs and return JSON-serializable metrics."""
    left = load_parse_run(left_dir)
    right = load_parse_run(right_dir)
    left_docs = _object_map(left.get("documents"))
    right_docs = _object_map(right.get("documents"))
    doc_ids = sorted(set(left_docs) | set(right_docs))
    rows: list[JsonObject] = []
    for doc_id in doc_ids:
        left_doc = left_docs.get(doc_id, {})
        right_doc = right_docs.get(doc_id, {})
        rows.append(
            {
                "doc_id": doc_id,
                "left": _comparison_metrics(left_doc),
                "right": _comparison_metrics(right_doc),
                "delta_right_minus_left": cast(
                    JsonValue, _numeric_delta(right_doc, left_doc)
                ),
            }
        )
    return {
        "same_corpus": bool(left.get("corpus_fingerprint") == right.get("corpus_fingerprint")),
        "corpus_fingerprint_left": left.get("corpus_fingerprint"),
        "corpus_fingerprint_right": right.get("corpus_fingerprint"),
        "left": {key: value for key, value in left.items() if key != "documents"},
        "right": {key: value for key, value in right.items() if key != "documents"},
        "documents": cast(JsonValue, rows),
    }


def _comparison_metrics(row: JsonObject) -> JsonObject:
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


def _numeric_delta(right: JsonObject, left: JsonObject) -> dict[str, float]:
    keys = (
        "basic_sense_score", "coverage_ratio", "llm_call_count", "total_tokens",
        "total_cost", "time_ms", "proposal_retries", "review_retries",
        "boundaries_rejected", "boundaries_shifted", "unresolved_intervals",
    )
    result: dict[str, float] = {}
    for key in keys:
        try:
            if right.get(key) is not None and left.get(key) is not None:
                right_value = _float_value(right.get(key))
                left_value = _float_value(left.get(key))
                if right_value is not None and left_value is not None:
                    result[key] = right_value - left_value
        except (TypeError, ValueError):
            continue
    return result


def render_parse_comparison_markdown(comparison: JsonObject) -> str:
    """Render a compact human-review table from ``compare_parse_runs``."""
    left = _as_object(comparison.get("left"))
    right = _as_object(comparison.get("right"))
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
    for row in _objects(comparison.get("documents")):
        for side in ("left", "right"):
            metrics = _as_object(row.get(side))
            lines.append(
                "| {doc} | {model} | {status} | {score} | {calls} | {tokens} | {cost} | {time} | {rejects} | {retries} |".format(
                    doc=row["doc_id"], model=(left if side == "left" else right).get("model"),
                    status=metrics.get("status"), score=metrics.get("basic_sense_score"),
                    calls=metrics.get("llm_call_count"), tokens=metrics.get("total_tokens"),
                    cost=metrics.get("total_cost"), time=metrics.get("time_ms"),
                    rejects=metrics.get("boundaries_rejected"),
                    retries=_int_value(metrics.get("proposal_retries"))
                    + _int_value(metrics.get("review_retries")),
                )
            )
    return "\n".join(lines) + "\n"


def write_parse_comparison(
    left_dir: str | Path,
    right_dir: str | Path,
    output_dir: str | Path,
) -> JsonObject:
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
