from __future__ import annotations

import re

import pytest
import tests.conftest as test_conf


pytestmark = pytest.mark.ci


def test_longrun_pgvector_database_name_ignores_budget_only_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_WORKSPACE_ID", "demo")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "parse_first")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER", "workflow_layered")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "medium")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", "ollama")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_MODEL", "gemma4:e2b")
    baseline = test_conf._longrun_pgvector_database_name()

    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAX_RUNTIME_SECONDS", "2400")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAX_LLM_CALLS", "250")
    assert test_conf._longrun_pgvector_database_name() == baseline


def test_longrun_pgvector_database_name_changes_with_operation_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_WORKSPACE_ID", "demo")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER", "workflow_layered")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "medium")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", "ollama")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_MODEL", "gemma4:e2b")

    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "parse_first")
    parse_first = test_conf._longrun_pgvector_database_name()
    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "maintenance_first")
    maintenance_first = test_conf._longrun_pgvector_database_name()
    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "hybrid")
    hybrid = test_conf._longrun_pgvector_database_name()

    assert len({parse_first, maintenance_first, hybrid}) == 3
    assert all(re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) for name in {parse_first, maintenance_first, hybrid})


def test_longrun_pgvector_database_name_ignores_run_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_WORKSPACE_ID", "demo")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "parse_first")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER", "workflow_layered")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "medium")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", "ollama")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_MODEL", "gemma4:e2b")

    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    fresh = test_conf._longrun_pgvector_database_name()
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "continue")
    continue_name = test_conf._longrun_pgvector_database_name()
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "auto")
    auto = test_conf._longrun_pgvector_database_name()

    assert fresh == continue_name == auto


def test_longrun_pgvector_shared_database_name_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "shared")
    monkeypatch.delenv("KOGWISTAR_LONGRUN_PG_DATABASE_NAME", raising=False)

    baseline = test_conf._longrun_pgvector_database_name()
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAX_RUNTIME_SECONDS", "7200")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAX_LLM_CALLS", "500")
    assert test_conf._longrun_pgvector_database_name() == baseline
    assert baseline == "kogwistar_longrun_shared"
