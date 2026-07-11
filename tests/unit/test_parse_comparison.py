import json

from kogwistar_llm_wiki.parse_comparison import write_parse_comparison


def _make_run(root, model, score, cost):
    dump = root / "dump"
    logs = dump / "parser_layer_logs"
    logs.mkdir(parents=True)
    (dump / "run_config.json").write_text(
        json.dumps({"parser_model": model, "parser_provider": "azure", "corpus_fingerprint": "same"}),
        encoding="utf-8",
    )
    (dump / "progress_summary.json").write_text(
        json.dumps({"completed_count": 1, "failed_count": 0, "parser_eval": {
            "documents": [{"doc_id": "doc-001", "basic_sense_score": score, "coverage_ratio": 1.0}],
            "usage_documents": [{"doc_id": "doc-001", "llm_call_count": 2, "total_tokens": 1000, "total_cost": cost, "time_ms": 5000}],
        }}),
        encoding="utf-8",
    )
    (dump / "manifest.jsonl").write_text(json.dumps({"doc_id": "doc-001", "status": "COMPLETED"}) + "\n", encoding="utf-8")
    (logs / "doc-001.json").write_text(json.dumps([
        {"stage": "workflow_layered_proposal_result", "retry_count": 1, "boundary_count": 3, "rejected_boundary_count": 0}
    ]), encoding="utf-8")


def test_write_parse_comparison_persists_matched_metrics(tmp_path):
    left = tmp_path / "nano"
    right = tmp_path / "mini"
    _make_run(left, "gpt-5-nano", 60.0, 0.01)
    _make_run(right, "gpt-5-mini", 70.0, 0.05)

    comparison = write_parse_comparison(left, right, tmp_path / "comparison")

    assert comparison["same_corpus"] is True
    assert comparison["documents"][0]["delta_right_minus_left"]["total_cost"] == 0.04
    assert (tmp_path / "comparison" / "comparison.md").exists()
