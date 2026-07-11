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


@pytest.mark.parametrize(
    ("variable", "first", "second"),
    [
        ("KOGWISTAR_LONGRUN_CORPUS_PROFILE", "daily_life", "watershed_stress"),
        ("KOGWISTAR_LONGRUN_PARSER_PROPOSAL_MODE", "children", "boundaries"),
        ("KOGWISTAR_LONGRUN_PARSER_WORKERS", "1", "2"),
        ("KOGWISTAR_LONGRUN_TOKEN_MIN", "500", "700"),
        ("KOGWISTAR_LONGRUN_TOKEN_MAX", "2000", "3000"),
    ],
)
def test_longrun_pgvector_database_name_changes_with_experiment_dimension(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    first: str,
    second: str,
) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_WORKSPACE_ID", "demo")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "parse_first")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER", "workflow_layered")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "medium")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", "ollama")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_MODEL", "gemma4:e2b")
    monkeypatch.setenv(variable, first)
    baseline = test_conf._longrun_pgvector_database_name()
    monkeypatch.setenv(variable, second)
    assert test_conf._longrun_pgvector_database_name() != baseline


def test_fresh_fingerprint_managed_database_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint")
    reset_calls: list[tuple[str, str]] = []
    ensure_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        test_conf,
        "_reset_pgvector_database",
        lambda dsn, name: reset_calls.append((dsn, name)) or f"reset:{name}",
    )
    monkeypatch.setattr(
        test_conf,
        "_ensure_pgvector_database",
        lambda dsn, name: ensure_calls.append((dsn, name)) or f"ensure:{name}",
    )

    assert test_conf._prepare_longrun_pgvector_database("dsn", "db", pg_source="persistent") == "reset:db"
    assert reset_calls == [("dsn", "db")]
    assert ensure_calls == []


@pytest.mark.parametrize(
    ("run_mode", "database_mode", "pg_source"),
    [
        ("continue", "fingerprint", "persistent"),
        ("retry_failed", "fingerprint", "persistent"),
        ("auto", "fingerprint", "testcontainer"),
        ("fresh", "shared", "persistent"),
        ("fresh", "fingerprint", "custom"),
    ],
)
def test_non_destructive_pgvector_modes_preserve_database(
    monkeypatch: pytest.MonkeyPatch,
    run_mode: str,
    database_mode: str,
    pg_source: str,
) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", run_mode)
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", database_mode)
    reset_calls: list[tuple[str, str]] = []
    ensure_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        test_conf,
        "_reset_pgvector_database",
        lambda dsn, name: reset_calls.append((dsn, name)) or f"reset:{name}",
    )
    monkeypatch.setattr(
        test_conf,
        "_ensure_pgvector_database",
        lambda dsn, name: ensure_calls.append((dsn, name)) or f"ensure:{name}",
    )

    assert test_conf._prepare_longrun_pgvector_database("dsn", "db", pg_source=pg_source) == "ensure:db"
    assert reset_calls == []
    assert ensure_calls == [("dsn", "db")]
