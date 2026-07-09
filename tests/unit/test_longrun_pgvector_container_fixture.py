from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_test_conftest():
    path = Path(__file__).resolve().parents[1] / "conftest.py"
    spec = importlib.util.spec_from_file_location("llm_wiki_tests_conftest", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


test_conftest = _load_test_conftest()


class _DummyConfig:
    def __init__(self, options: dict[str, object] | None = None):
        self.options = dict(options or {})

    def getoption(self, name: str, default=None):
        return self.options.get(name, default)


class _DummyItem:
    def __init__(self, keywords: dict[str, object]):
        self.keywords = keywords
        self.markers: list[object] = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


def _marker_names(item: _DummyItem) -> list[str]:
    names: list[str] = []
    for marker in item.markers:
        name = getattr(getattr(marker, "mark", None), "name", None)
        if name is not None:
            names.append(name)
    return names


def test_longrun_requested_accepts_pytest_option_env_or_probe(monkeypatch):
    monkeypatch.delenv("KOGWISTAR_LLM_WIKI_LONGRUN", raising=False)

    assert test_conftest._longrun_requested(_DummyConfig()) is False
    assert test_conftest._longrun_requested(_DummyConfig({"kogwistar_longrun": True})) is True
    assert test_conftest._longrun_requested(
        _DummyConfig({"kogwistar_longrun_probe": "pgvector-testcontainer"})
    ) is True

    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    assert test_conftest._longrun_requested(_DummyConfig()) is True


def test_longrun_requested_ignores_backend_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("KOGWISTAR_LLM_WIKI_LONGRUN", raising=False)
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")

    assert test_conftest._longrun_requested(_DummyConfig()) is False


def test_mark_disabled_longrun_items_skips_only_longrun_when_not_enabled(monkeypatch):
    monkeypatch.delenv("KOGWISTAR_LLM_WIKI_LONGRUN", raising=False)
    longrun_item = _DummyItem({"longrun": True})
    ordinary_item = _DummyItem({})

    test_conftest._mark_disabled_longrun_items(_DummyConfig(), [longrun_item, ordinary_item])

    assert _marker_names(longrun_item) == ["skip"]
    assert _marker_names(ordinary_item) == []


def test_mark_disabled_longrun_items_leaves_enabled_longrun_unmarked(monkeypatch):
    monkeypatch.delenv("KOGWISTAR_LLM_WIKI_LONGRUN", raising=False)
    longrun_item = _DummyItem({"longrun": True})

    test_conftest._mark_disabled_longrun_items(
        _DummyConfig({"kogwistar_longrun": True}),
        [longrun_item],
    )

    assert _marker_names(longrun_item) == []


def test_append_longrun_skip_notice_writes_console_and_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(test_conftest, "LONGRUN_SKIP_LOG_PATH", tmp_path / "longrun-skip.log")

    test_conftest._append_longrun_skip_notice("needs explicit opt-in")

    captured = capsys.readouterr()
    assert "[longrun.skip] needs explicit opt-in" in captured.err
    assert "needs explicit opt-in" in test_conftest.LONGRUN_SKIP_LOG_PATH.read_text(encoding="utf-8")


def test_configure_longrun_testcontainers_ryuk_env_defaults_enabled(monkeypatch):
    monkeypatch.delenv("GKE_TEST_PG_DISABLE_RYUK", raising=False)
    monkeypatch.delenv("TESTCONTAINERS_RYUK_DISABLED", raising=False)

    disabled = test_conftest._configure_longrun_testcontainers_ryuk_env()

    assert disabled is False
    assert "TESTCONTAINERS_RYUK_DISABLED" not in test_conftest.os.environ


def test_configure_longrun_testcontainers_ryuk_env_honors_guard(monkeypatch):
    monkeypatch.setenv("GKE_TEST_PG_DISABLE_RYUK", "1")
    monkeypatch.delenv("TESTCONTAINERS_RYUK_DISABLED", raising=False)

    disabled = test_conftest._configure_longrun_testcontainers_ryuk_env()

    assert disabled is True
    assert test_conftest.os.environ["TESTCONTAINERS_RYUK_DISABLED"] == "true"


def test_is_ryuk_port_mapping_failure_matches_expected_error():
    exc = RuntimeError("Port mapping for container abc and port 8080 is not available")
    assert test_conftest._is_ryuk_port_mapping_failure(exc) is True


def test_is_ryuk_port_mapping_failure_ignores_other_errors():
    exc = RuntimeError("docker unavailable")
    assert test_conftest._is_ryuk_port_mapping_failure(exc) is False


def test_start_longrun_pgvector_container_propagates_constructor_failure():
    class FailingContainer:
        def __init__(self, image: str):
            raise RuntimeError(f"docker unavailable for {image}")

    with pytest.raises(RuntimeError, match="docker unavailable"):
        test_conftest._start_longrun_pgvector_container(FailingContainer, "pgvector/pgvector:pg17")


def test_start_longrun_pgvector_container_stops_partial_container_on_start_failure():
    class PartialContainer:
        stopped = False

        def __init__(self, image: str):
            self.image = image

        def start(self):
            raise RuntimeError("start failed")

        def stop(self):
            type(self).stopped = True

    with pytest.raises(RuntimeError, match="start failed"):
        test_conftest._start_longrun_pgvector_container(PartialContainer, "pgvector/pgvector:pg17")

    assert PartialContainer.stopped is True


def test_start_longrun_pgvector_container_returns_started_container():
    class StartedContainer:
        def __init__(self, image: str):
            self.image = image
            self.started = False

        def start(self):
            self.started = True

    container = test_conftest._start_longrun_pgvector_container(
        StartedContainer,
        "pgvector/pgvector:pg17",
    )

    assert container.started is True
    assert container.image == "pgvector/pgvector:pg17"


def test_apply_longrun_probe_env_materializes_pgvector_testcontainer(monkeypatch):
    for key in (
        "KOGWISTAR_LLM_WIKI_LONGRUN",
        "KOGWISTAR_LONGRUN_MODE",
        "KOGWISTAR_LONGRUN_BACKEND",
        "KOGWISTAR_LONGRUN_PG_SOURCE",
        "KOGWISTAR_LONGRUN_PARSER",
        "KOGWISTAR_LONGRUN_DOC_COUNT",
        "KOGWISTAR_LONGRUN_RUN_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    test_conftest._apply_longrun_probe_env("pgvector-testcontainer")

    assert test_conftest.os.environ["KOGWISTAR_LLM_WIKI_LONGRUN"] == "1"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_MODE"] == "fresh"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_BACKEND"] == "pgvector"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_PG_SOURCE"] == "testcontainer"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_PARSER"] == "page_index"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_DOC_COUNT"] == "1"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_RUN_DIR"].endswith(
        "tests\\_tmp\\longrun-vscode-pgvector-probe"
    )


def test_apply_longrun_probe_env_materializes_pgvector_persistent(monkeypatch):
    for key in (
        "KOGWISTAR_LLM_WIKI_LONGRUN",
        "KOGWISTAR_LONGRUN_MODE",
        "KOGWISTAR_LONGRUN_BACKEND",
        "KOGWISTAR_LONGRUN_PG_SOURCE",
        "KOGWISTAR_LONGRUN_PARSER",
        "KOGWISTAR_LONGRUN_DOC_COUNT",
        "KOGWISTAR_LONGRUN_RUN_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    test_conftest._apply_longrun_probe_env("pgvector-persistent")

    assert test_conftest.os.environ["KOGWISTAR_LLM_WIKI_LONGRUN"] == "1"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_BACKEND"] == "pgvector"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_PG_SOURCE"] == "persistent"
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_RUN_DIR"].endswith(
        "tests\\_tmp\\longrun-vscode-pgvector-persistent-probe"
    )


def test_apply_longrun_probe_env_rejects_unknown_probe():
    with pytest.raises(ValueError, match="Unsupported --kogwistar-longrun-probe"):
        test_conftest._apply_longrun_probe_env("unknown")


def test_ensure_longrun_persistent_pgvector_container_reuses_running_container(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_CONTAINER_NAME", "kg-dev")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_PORT", "35432")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_USER", "postgres")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_PASSWORD", "postgres")

    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(args: list[str]):
        calls.append(args)
        return _Result(0, stdout="running\n")

    monkeypatch.setattr(test_conftest, "_run_longrun_docker_command", _fake_run)

    dsn = test_conftest._ensure_longrun_persistent_pgvector_container("pgvector/pgvector:pg17")

    assert dsn == "postgresql+psycopg://postgres:postgres@127.0.0.1:35432/postgres"
    assert calls == [["container", "inspect", "kg-dev", "--format", "{{.State.Status}}"]]


def test_ensure_longrun_persistent_pgvector_container_starts_stopped_container(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_CONTAINER_NAME", "kg-dev")
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(args: list[str]):
        calls.append(args)
        if args[:2] == ["container", "inspect"]:
            return _Result(0, stdout="exited\n")
        return _Result(0, stdout="kg-dev\n")

    monkeypatch.setattr(test_conftest, "_run_longrun_docker_command", _fake_run)

    dsn = test_conftest._ensure_longrun_persistent_pgvector_container("pgvector/pgvector:pg17")

    assert dsn.endswith("@127.0.0.1:35432/postgres")
    assert calls == [
        ["container", "inspect", "kg-dev", "--format", "{{.State.Status}}"],
        ["start", "kg-dev"],
    ]


def test_ensure_longrun_persistent_pgvector_container_unpauses_paused_container(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_CONTAINER_NAME", "kg-dev")
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(args: list[str]):
        calls.append(args)
        if args[:2] == ["container", "inspect"]:
            return _Result(0, stdout="paused\n")
        return _Result(0, stdout="kg-dev\n")

    monkeypatch.setattr(test_conftest, "_run_longrun_docker_command", _fake_run)

    dsn = test_conftest._ensure_longrun_persistent_pgvector_container("pgvector/pgvector:pg17")

    assert dsn.endswith("@127.0.0.1:35432/postgres")
    assert calls == [
        ["container", "inspect", "kg-dev", "--format", "{{.State.Status}}"],
        ["unpause", "kg-dev"],
    ]


def test_ensure_longrun_persistent_pgvector_container_creates_missing_container(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_CONTAINER_NAME", "kg-dev")
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(args: list[str]):
        calls.append(args)
        if args[:2] == ["container", "inspect"]:
            return _Result(1, stderr="No such container")
        return _Result(0, stdout="new-container-id\n")

    monkeypatch.setattr(test_conftest, "_run_longrun_docker_command", _fake_run)

    dsn = test_conftest._ensure_longrun_persistent_pgvector_container("pgvector/pgvector:pg17")

    assert dsn.endswith("@127.0.0.1:35432/postgres")
    assert calls[0][:3] == ["container", "inspect", "kg-dev"]
    assert calls[1][:4] == ["run", "-d", "--name", "kg-dev"]


def test_longrun_pgvector_fixture_uses_persistent_container_without_teardown(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_SOURCE", "persistent")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint")

    ensure_calls: list[object] = []

    monkeypatch.setattr(
        test_conftest,
        "_ensure_longrun_persistent_pgvector_container",
        lambda image: (ensure_calls.append(image) or "postgresql://user:pass@127.0.0.1:35432/postgres"),
    )
    monkeypatch.setattr(test_conftest, "_ensure_pgvector_database_with_retry", lambda dsn, database_name: dsn)
    monkeypatch.setattr(test_conftest.logger, "info", lambda *args, **kwargs: None)

    fixture = test_conftest._longrun_pgvector_testcontainer.__wrapped__
    gen = fixture(_DummyConfig({"kogwistar_longrun": True}))

    assert next(gen) is None
    assert ensure_calls == ["pgvector/pgvector:pg17"]
    assert test_conftest.os.environ["KOGWISTAR_LONGRUN_DSN"].startswith("postgresql+psycopg://")

    gen.close()


def test_longrun_pgvector_fixture_retries_without_ryuk_on_8080_mapping_failure(monkeypatch):
    monkeypatch.delenv("GKE_TEST_PG_DISABLE_RYUK", raising=False)
    monkeypatch.delenv("TESTCONTAINERS_RYUK_DISABLED", raising=False)
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_SOURCE", "testcontainer")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint")

    class _FakeContainer:
        def get_connection_url(self) -> str:
            return "postgresql://user:pass@localhost:5432/postgres"

        def stop(self) -> None:
            return None

    start_calls: list[str] = []

    def _fake_start(container_cls, image: str):
        start_calls.append(image)
        if len(start_calls) == 1:
            raise RuntimeError("Port mapping for container abc and port 8080 is not available")
        return _FakeContainer()

    load_calls: list[str] = []

    def _fake_load():
        load_calls.append("load")
        return object()

    monkeypatch.setattr(test_conftest, "_load_longrun_postgres_container_cls", _fake_load)
    monkeypatch.setattr(test_conftest, "_start_longrun_pgvector_container", _fake_start)
    monkeypatch.setattr(test_conftest, "_purge_testcontainers_modules", lambda: load_calls.append("purge"))
    monkeypatch.setattr(test_conftest, "_ensure_pgvector_database", lambda dsn, database_name: dsn)
    monkeypatch.setattr(test_conftest.logger, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(test_conftest.logger, "info", lambda *args, **kwargs: None)

    fixture = test_conftest._longrun_pgvector_testcontainer.__wrapped__
    gen = fixture(_DummyConfig({"kogwistar_longrun": True}))

    assert next(gen) is None
    assert len(start_calls) == 2
    assert test_conftest.os.environ["TESTCONTAINERS_RYUK_DISABLED"] == "true"
    assert load_calls == ["load", "purge", "load"]

    gen.close()
