from __future__ import annotations

from types import SimpleNamespace

import pytest

import kogwistar_llm_wiki.maintenance.observation_critic as critic_module
from kogwistar_llm_wiki.daemons.maintenance_daemon import MaintenanceDaemonRuntime
from kogwistar_llm_wiki.maintenance.maintenance_control import MaintenanceControl
from kogwistar_llm_wiki.maintenance.maintenance_observation import (
    MaintenanceObservationFrame,
    ObservationFinding,
    ObservationSubject,
    assess_observation_frame,
    build_observation_frame,
)
from kogwistar_llm_wiki.maintenance.observation_critic import (
    build_observation_critic,
    is_context_window_error,
)
from kogwistar_llm_wiki.maintenance.worker_observation import (
    MaintenanceObservationWorkerMixin,
)
from kogwistar_llm_wiki.worker import MaintenanceWorker


class _StructuredResponse:
    def __init__(self, output: object) -> None:
        self.output = output

    def invoke(self, _messages: object) -> dict[str, object]:
        return {"parsed": self.output}


class _Model:
    def __init__(self, output: object) -> None:
        self.output = output
        self.schema: type | None = None

    def with_structured_output(self, schema: type) -> _StructuredResponse:
        self.schema = schema
        return _StructuredResponse(self.output)


class _FailingResponse:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def invoke(self, _messages: object) -> object:
        raise self.error


class _FailingModel:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def with_structured_output(self, _schema: type) -> _FailingResponse:
        return _FailingResponse(self.error)


def _frame() -> MaintenanceObservationFrame:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id="demo",
        namespace="ws:demo:g:curated_kg",
    )
    return build_observation_frame(
        subject,
        source_context=[{"id": "node-1", "label": "Unknown chip"}],
    )


def test_provider_critic_returns_only_schema_valid_evidence_bound_findings(monkeypatch) -> None:
    finding = ObservationFinding(
        code="weak_label",
        verdict="weak_label",
        severity="warning",
        subject_id="node-1",
        evidence_ids=("node-1",),
        message="The label is underspecified.",
        recommended_action="review_parent",
    )
    model = _Model({"findings": [finding.model_dump(mode="json")]})
    monkeypatch.setattr(critic_module, "build_chat_model", lambda *_args: model)
    critic = build_observation_critic(SimpleNamespace(parser=object()))

    result = critic(_frame(), SimpleNamespace())

    assert result == {"status": "succeeded", "findings": [finding]}
    assert model.schema is not None


@pytest.mark.parametrize(
    "finding_data",
    [
        {
            "code": "weak_label",
            "verdict": "weak_label",
            "severity": "warning",
            "subject_id": "different-node",
            "evidence_ids": ["node-1"],
            "message": "wrong target",
        },
        {
            "code": "ungrounded",
            "verdict": "ungrounded",
            "severity": "error",
            "subject_id": "node-1",
            "evidence_ids": ["invented-id"],
            "message": "invented evidence",
        },
        {
            "code": "weak_label",
            "verdict": "weak_label",
            "severity": "warning",
            "subject_id": "node-1",
            "evidence_ids": [],
            "message": "no cited evidence",
        },
    ],
)
def test_provider_critic_rejects_wrong_subject_or_ungrounded_findings(
    monkeypatch, finding_data: dict[str, object]
) -> None:
    model = _Model({"findings": [finding_data]})
    monkeypatch.setattr(critic_module, "build_chat_model", lambda *_args: model)
    critic = build_observation_critic(SimpleNamespace(parser=object()))

    with pytest.raises(ValueError):
        critic(_frame(), SimpleNamespace())


def test_provider_critic_builds_provider_lazily_and_reuses_it(monkeypatch) -> None:
    builds: list[object] = []
    model = _Model({"findings": []})
    monkeypatch.setattr(
        critic_module,
        "build_chat_model",
        lambda *_args: builds.append(object()) or model,
    )
    critic = build_observation_critic(SimpleNamespace(parser=object()))

    assert builds == []
    critic(_frame(), SimpleNamespace())
    critic(_frame(), SimpleNamespace())

    assert len(builds) == 1


def test_critic_disables_adapter_retries_independent_of_provider(monkeypatch) -> None:
    from kg_doc_parser.workflow_ingest.providers import ProviderEndpointConfig

    captured: list[object] = []
    model = _Model({"findings": []})
    monkeypatch.setattr(
        critic_module,
        "build_chat_model",
        lambda spec: captured.append(spec) or model,
    )
    critic = build_observation_critic(
        SimpleNamespace(parser=ProviderEndpointConfig(provider="openai", max_retries=4))
    )

    critic(_frame(), SimpleNamespace())

    assert captured[0].provider == "openai"
    assert captured[0].max_retries == 0


def test_provider_change_rebuilds_default_critic_but_preserves_injected_critic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built_for: list[object] = []
    replacement_critic = lambda *_args: {"status": "succeeded", "findings": []}
    monkeypatch.setattr(
        "kogwistar_llm_wiki.worker.build_observation_critic",
        lambda settings: built_for.append(settings) or replacement_critic,
    )
    provider_settings = SimpleNamespace(parser=object())

    default_worker = SimpleNamespace(_uses_default_observation_critic=True)
    MaintenanceWorker.set_provider_settings(default_worker, provider_settings)
    assert default_worker.provider_settings is provider_settings
    assert default_worker.observation_critic is replacement_critic
    assert built_for == [provider_settings]

    custom_critic = lambda *_args: {"status": "succeeded", "findings": []}
    injected_worker = SimpleNamespace(
        _uses_default_observation_critic=False,
        observation_critic=custom_critic,
    )
    MaintenanceWorker.set_provider_settings(injected_worker, provider_settings)
    assert injected_worker.observation_critic is custom_critic


@pytest.mark.parametrize(
    "message",
    [
        "maximum context length is 4768 tokens",
        "prompt is too long for this model",
        "request exceeds the available context size (n_ctx)",
        "input token count exceeds the maximum number of tokens",
    ],
)
def test_context_window_errors_are_detected_across_provider_wording(message: str) -> None:
    assert is_context_window_error(RuntimeError(message))


def test_context_error_in_cause_is_detected() -> None:
    try:
        try:
            raise ValueError("maximum context length exceeded")
        except ValueError as cause:
            raise RuntimeError("provider request failed") from cause
    except RuntimeError as error:
        assert is_context_window_error(error)


def test_provider_specific_context_overflow_exception_type_is_detected() -> None:
    class AnthropicContextOverflowError(Exception):
        pass

    assert is_context_window_error(AnthropicContextOverflowError("request rejected"))


def test_context_limit_blocks_critic_and_pauses_durable_background_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        critic_module,
        "build_chat_model",
        lambda *_args: _FailingModel(RuntimeError("maximum context length exceeded")),
    )
    critic = build_observation_critic(SimpleNamespace(parser=object()))
    control = MaintenanceControl(tmp_path)
    control.update(background_enabled=True)
    traces: list[dict[str, object]] = []
    worker = SimpleNamespace(
        observation_critic=critic,
        context_limit_sink=lambda: MaintenanceDaemonRuntime._stop_for_context_limit(
            SimpleNamespace(
                control=control,
                control_state=control.get(),
                _worker=SimpleNamespace(),
            )
        ),
        _emit_trace=lambda event, **fields: traces.append({"event": event, **fields}),
    )
    ctx = SimpleNamespace(
        workspace_id="demo",
        job_id="job-context-overflow",
        payload={"budgets": {"max_llm_calls": 1}},
    )

    status, _ = MaintenanceObservationWorkerMixin._run_observation_critic(worker, ctx, _frame())
    assessment = assess_observation_frame(_frame(), critic_status=status)

    persisted = MaintenanceControl(tmp_path).get()
    assert status == "blocked_context"
    assert assessment.critic_status == "blocked_context"
    assert assessment.continuation_allowed is False
    assert persisted.background_enabled is False
    assert persisted.request_enabled is False
    assert persisted.status_reason == "blocked_context_window"
    restarted_control = MaintenanceControl(tmp_path)
    assert restarted_control.get().request_enabled is False
    assert restarted_control.get().background_enabled is False
    assert any(item["event"] == "maintenance_observation_context_limit_blocked" for item in traces)


def test_parser_context_overflow_marks_job_final_and_does_not_retry() -> None:
    job = SimpleNamespace(
        job_id="job-parse-overflow",
        payload={"mode": "background"},
        claim_token="claim-parse-overflow",
    )
    failed: list[tuple[object, ...]] = []
    retried: list[object] = []
    paused: list[bool] = []
    traces: list[dict[str, object]] = []

    class Jobs:
        def __init__(self) -> None:
            self.claims = [[job], []]

        def require_available(self, **_kwargs: object) -> None:
            return None

        def claim(self, **_kwargs: object) -> list[object]:
            return self.claims.pop(0)

        def mark_failed(self, *args: object, **kwargs: object) -> None:
            failed.append((*args, kwargs))

        def retry_or_fail(self, claimed_job: object, _error: Exception) -> None:
            retried.append(claimed_job)

    worker = object.__new__(MaintenanceWorker)
    jobs = Jobs()
    worker.engines = SimpleNamespace(conversation=SimpleNamespace(jobs=jobs))
    worker.fair_scheduling = False
    worker.background_enabled = True
    worker.request_enabled = True
    worker.context_limit_sink = lambda: paused.append(True)
    worker._emit_trace = lambda event, **fields: traces.append({"event": event, **fields})
    worker._handle_job = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("maximum context length exceeded (llama.cpp n_ctx)")
    )

    MaintenanceWorker.process_pending_jobs(worker, "demo")

    assert len(failed) == 1
    assert failed[0][0] == "job-parse-overflow"
    assert failed[0][2] == {"final": True, "claim_token": "claim-parse-overflow"}
    assert retried == []
    assert paused == [True]
    assert worker.background_enabled is False
    assert worker.request_enabled is False
    assert any(item["event"] == "maintenance_job_failed_context_limit" for item in traces)


def test_worker_observation_critic_consumes_job_call_budget_once() -> None:
    calls: list[object] = []
    traces: list[dict[str, object]] = []
    worker = SimpleNamespace(
        observation_critic=lambda _frame, _ctx: calls.append(object())
        or {"status": "succeeded", "findings": []},
        _emit_trace=lambda event, **fields: traces.append({"event": event, **fields}),
    )
    ctx = SimpleNamespace(
        workspace_id="demo",
        job_id="job-1",
        payload={"budgets": {"max_llm_calls": 1}},
    )

    first_status, first_findings = MaintenanceObservationWorkerMixin._run_observation_critic(
        worker, ctx, _frame()
    )
    second_status, second_findings = MaintenanceObservationWorkerMixin._run_observation_critic(
        worker, ctx, _frame()
    )

    assert first_status == "succeeded" and first_findings == ()
    assert second_status == "failed" and second_findings == ()
    assert len(calls) == 1
    assert ctx.payload["maintenance_budget_state"]["call_used"] == 1
    assert traces[0]["event"] == "maintenance_observation_critic_budget_exhausted"


def test_unavailable_expansion_session_does_not_enqueue_continuation() -> None:
    traces: list[dict[str, object]] = []
    worker = SimpleNamespace(
        _emit_trace=lambda event, **fields: traces.append({"event": event, **fields}),
    )
    ctx = SimpleNamespace(
        workspace_id="demo",
        request_node_id="request-1",
        job_id="job-1",
        payload={
            "maintenance_round": 0,
            "maintenance_max_rounds": 2,
            "budgets": {"max_steps": 2},
            "maintenance_budget_state": {"step_used": 0},
        },
    )

    scheduled = MaintenanceObservationWorkerMixin._schedule_observation_continuation(
        worker, ctx, "expand_children", True
    )

    assert scheduled is False
    assert traces == [
        {
            "event": "maintenance_observation_continuation_blocked",
            "workspace_id": "demo",
            "job_id": "job-1",
            "action": "expand_children",
            "reason": "durable_parse_session_missing",
        }
    ]
