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
