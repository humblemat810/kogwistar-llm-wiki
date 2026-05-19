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


def test_apply_longrun_probe_env_rejects_unknown_probe():
    with pytest.raises(ValueError, match="Unsupported --kogwistar-longrun-probe"):
        test_conftest._apply_longrun_probe_env("unknown")
