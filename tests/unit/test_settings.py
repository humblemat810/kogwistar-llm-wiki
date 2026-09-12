import json

import pytest

from kogwistar_llm_wiki import IngestPipeline, WorkbenchApi, build_in_memory_namespace_engines
from kogwistar_llm_wiki.settings import SettingsService


@pytest.fixture(autouse=True)
def _isolated_settings_environment(monkeypatch):
    """Keep settings contract tests independent of a developer's .env file."""
    monkeypatch.setenv("LLM_WIKI_AUTH_MODE", "disabled")


def test_settings_snapshot_reports_effective_profiles_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("KOGWISTAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "do-not-return")
    engines = build_in_memory_namespace_engines()
    try:
        snapshot = WorkbenchApi(IngestPipeline(engines)).get_settings(workspace_id="demo")
        assert snapshot["effective"]["workspace_id"] == "demo"
        assert snapshot["effective"]["backend"]
        assert snapshot["effective"]["embeddings"]["conversation"]["profile"]["dimension"] == 2
        assert "do-not-return" not in json.dumps(snapshot)
    finally:
        engines.close()


def test_desired_settings_are_persisted_and_model_change_requires_restart(tmp_path):
    engines = build_in_memory_namespace_engines()
    try:
        service = SettingsService(IngestPipeline(engines), path=tmp_path / "desired.json")
        snapshot = service.update_desired({"parser_model": "gpt-5-mini"}, workspace_id="demo")
        assert snapshot["desired"] == {"parser_model": "gpt-5-mini"}
        assert snapshot["restart_required"] is True
        assert json.loads((tmp_path / "desired.json").read_text())["settings"] == snapshot["desired"]
    finally:
        engines.close()


def test_multimodal_toggle_is_staged_without_reembedding_requirement(tmp_path):
    engines = build_in_memory_namespace_engines()
    try:
        service = SettingsService(IngestPipeline(engines), path=tmp_path / "desired.json")
        snapshot = service.update_desired({"multimodal_enabled": True}, workspace_id="demo")
        assert snapshot["desired"]["multimodal_enabled"] is True
        assert snapshot["reembedding_required"] is False
        assert snapshot["restart_required"] is False
    finally:
        engines.close()


def test_unknown_or_invalid_desired_settings_are_rejected(tmp_path):
    engines = build_in_memory_namespace_engines()
    try:
        service = SettingsService(IngestPipeline(engines), path=tmp_path / "desired.json")
        with pytest.raises(ValueError, match="unsupported settings"):
            service.update_desired({"database_password": "secret"})
        with pytest.raises(ValueError, match="must be boolean"):
            service.update_desired({"multimodal_enabled": "yes"})
    finally:
        engines.close()


def test_apply_requires_confirmation_and_never_claims_restart_is_applied(tmp_path):
    engines = build_in_memory_namespace_engines()
    try:
        service = SettingsService(IngestPipeline(engines), path=tmp_path / "desired.json")
        service.update_desired({"maintenance_model": "gpt-5-mini"})
        pending = service.apply(workspace_id="demo", confirmed=False)
        assert pending["status"] == "confirmation_required"
        staged = service.apply(workspace_id="demo", confirmed=True)
        assert staged["status"] == "staged"
    finally:
        engines.close()


def test_settings_reports_otel_state_and_stages_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_WIKI_OTEL_ENABLED", "false")
    engines = build_in_memory_namespace_engines()
    try:
        api = WorkbenchApi(IngestPipeline(engines), settings_path=str(tmp_path / "desired.json"))
        snapshot = api.get_settings(workspace_id="demo")
        assert snapshot["effective"]["otel"]["enabled"] is False
        assert snapshot["components"]["otel_sink"]["toggleable"] is True
        updated = api.update_desired_settings(workspace_id="demo", changes={"otel_enabled": False})
        assert updated["desired"]["otel_enabled"] is False
        assert api.apply_settings(workspace_id="demo", confirmed=True)["status"] == "applied"
    finally:
        engines.close()


def test_settings_reports_the_effective_otlp_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_WIKI_OTEL_ENABLED", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    engines = build_in_memory_namespace_engines()
    try:
        snapshot = WorkbenchApi(
            IngestPipeline(engines), settings_path=str(tmp_path / "desired.json")
        ).get_settings(workspace_id="demo")
        assert snapshot["effective"]["otel"]["configured"] is True
        assert snapshot["effective"]["otel"]["endpoint"] == "http://collector:4318/v1/traces"
    finally:
        engines.close()


def test_auth_mode_is_staged_and_requires_restart(tmp_path):
    engines = build_in_memory_namespace_engines()
    try:
        service = SettingsService(IngestPipeline(engines), path=tmp_path / "desired.json")
        snapshot = service.update_desired({"auth_mode": "kogwistar_jwt"})
        assert snapshot["desired"]["auth_mode"] == "kogwistar_jwt"
        assert snapshot["restart_required"] is True
        assert service.apply(workspace_id="default", confirmed=True)["status"] == "staged"
    finally:
        engines.close()
