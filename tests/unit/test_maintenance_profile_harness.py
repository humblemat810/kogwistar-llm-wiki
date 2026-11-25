from __future__ import annotations

import os

from scripts.profile_maintenance_e2e import (
    ConversationSemanticVisibilityProbe,
    DeterministicEmbedding,
    _assessment_quality_summary,
    _local_critic_provider_settings,
    _trace_summary_line,
)


def test_deterministic_embedding_reports_call_and_item_timings() -> None:
    embedding = DeterministicEmbedding()

    first = embedding(["alpha", "beta"])
    second = embedding(["alpha"])

    assert first[0] == second[0]
    assert len(first) == 2
    assert len(second) == 1
    metrics = embedding.metrics()
    assert metrics["invocation_count"] == 2
    assert metrics["item_count"] == 3
    assert metrics["total_time_ms"] >= 0
    assert metrics["per_invocation_ms"]["count"] == 2
    assert "not model inference latency" in metrics["meaning"]


def test_streamed_trace_summary_excludes_payload_content() -> None:
    summary = _trace_summary_line(
        {
            "event": "maintenance_observation_completed",
            "workflow_stage": "critic",
            "duration_ms": 12.5,
            "error_type": "OutputParserException",
            "error_code": "structured_output_parse_failure",
            "assessment": {
                "critic_status": "succeeded",
                "verdict": "weak_label",
                "recommended_action": "review_parent",
                "findings": [
                    {
                        "severity": "warning",
                        "message": "must not be streamed",
                        "evidence_ids": ["must-not-be-streamed"],
                        "code": "must-not-be-streamed",
                    },
                    {"severity": "error", "message": "also private"},
                ],
            },
            "payload": {"source_text": "must not be streamed"},
            "source_text": "must not be streamed",
        },
        7,
    )

    assert summary["trace_sequence"] == 7
    assert isinstance(summary["observed_at_utc"], str)
    assert summary["event"] == "maintenance_observation_completed"
    assert summary["workflow_stage"] == "critic"
    assert summary["duration_ms"] == 12.5
    assert summary["error_type"] == "OutputParserException"
    assert summary["error_code"] == "structured_output_parse_failure"
    assert summary["critic_status"] == "succeeded"
    assert summary["assessment_verdict"] == "weak_label"
    assert summary["recommended_action"] == "review_parent"
    assert summary["finding_count"] == 2
    assert summary["finding_severity_counts"] == {"error": 1, "warning": 1}
    assert "message" not in summary
    assert "evidence_ids" not in summary
    assert "code" not in summary
    assert "payload" not in summary
    assert "source_text" not in summary


def test_assessment_quality_summary_aggregates_only_allowlisted_fields() -> None:
    summary = _assessment_quality_summary(
        [
            {
                "event": "maintenance_observation_completed",
                "assessment": {
                    "verdict": "weak_label",
                    "recommended_action": "request_human_review",
                    "findings": [
                        {
                            "verdict": "weak_label",
                            "severity": "info",
                            "message": "private text",
                            "evidence_ids": ["private-id"],
                        },
                        {"verdict": "unknown-value", "severity": "error"},
                    ],
                },
                "source_text": "private source",
            },
            {
                "event": "maintenance_observation_completed",
                "assessment": {
                    "verdict": "review_required",
                    "recommended_action": "review_parent",
                    "findings": [],
                },
            },
            {"event": "maintenance_job_acknowledged"},
        ]
    )

    assert summary == {
        "assessment_count": 2,
        "verdict_counts": {"review_required": 1, "weak_label": 1},
        "recommended_action_counts": {
            "request_human_review": 1,
            "review_parent": 1,
        },
        "finding_count": 2,
        "finding_severity_counts": {"error": 1, "info": 1},
        "finding_verdict_counts": {"weak_label": 1},
        "finding_text_persisted": False,
    }


def test_semantic_visibility_probe_reports_post_send_check_without_ids() -> None:
    class Probe(ConversationSemanticVisibilityProbe):
        def _is_semantically_visible(self, message_id: str) -> bool:
            assert message_id in {"visible-message", "not-visible-message"}
            return message_id == "visible-message"

    probe = Probe(engine=object(), namespace="profile-test")  # type: ignore[arg-type]
    probe.record_send_result("visible-message", 12.5)
    probe.record_send_result("not-visible-message", 25.0)

    metrics = probe.metrics()

    assert metrics["messages_sent"] == 2
    assert metrics["messages_semantically_visible_after_send"] == 1
    assert metrics["messages_not_visible_after_send"] == 1
    assert metrics["send_call_latency_ms"]["mean_ms"] == 18.75
    assert metrics["post_send_semantic_query_latency_ms"]["count"] == 2
    assert "synchronous post-return verification" in metrics["measurement"]
    assert metrics["message_ids_persisted"] is False


def test_llama_cpp_profile_uses_openai_compatible_endpoint_and_no_auth_key(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LLM_WIKI_PROFILE_LOCAL_OPENAI_API_KEY", raising=False)

    settings = _local_critic_provider_settings(
        provider="llama_cpp",
        model="bonsai2.gguf",
        base_url="http://127.0.0.1:8181/",
        timeout_seconds=60.0,
        context_tokens=4096,
        output_tokens=1024,
    )

    assert settings.parser.provider == "openai"
    assert settings.parser.base_url == "http://127.0.0.1:8181/v1"
    assert settings.parser.model == "bonsai2.gguf"
    assert settings.parser.api_key_env == "LLM_WIKI_PROFILE_LOCAL_OPENAI_API_KEY"
    assert settings.parser.ollama_num_ctx is None
    assert settings.parser.max_output_tokens == 1024
    assert os.getenv("LLM_WIKI_PROFILE_LOCAL_OPENAI_API_KEY") == "local-no-auth"


def test_ollama_profile_keeps_native_context_configuration() -> None:
    settings = _local_critic_provider_settings(
        provider="ollama",
        model="bonsai2",
        base_url="http://127.0.0.1:11434",
        timeout_seconds=60.0,
        context_tokens=2048,
        output_tokens=256,
    )

    assert settings.parser.provider == "ollama"
    assert settings.parser.base_url == "http://127.0.0.1:11434"
    assert settings.parser.ollama_num_ctx == 2048
