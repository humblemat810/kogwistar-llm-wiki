from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
KOGWISTAR_ROOT = ROOT / "kogwistar"
KG_DOC_PARSER_SRC = ROOT / "kg-doc-parser" / "src"
OBSIDIAN_SINK_ROOT = ROOT / "kogwistar-obsidian-sink"
TEST_TMP = ROOT / "tests" / "_tmp"

TEST_TMP.mkdir(parents=True, exist_ok=True)
for key in ("TMPDIR", "TEMP", "TMP"):
    os.environ[key] = str(TEST_TMP)


import pytest


logger = logging.getLogger(__name__)


def pytest_configure(config):
    del config
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    for key in ("TMPDIR", "TEMP", "TMP"):
        os.environ[key] = str(TEST_TMP)


@pytest.fixture()
def tmp_path():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP / f"kogwistar-llm-wiki-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

# Keep the local src tree first, but do not let vendored repos shadow the repo's own tests.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
for path in [KG_DOC_PARSER_SRC, KOGWISTAR_ROOT, OBSIDIAN_SINK_ROOT]:
    if str(path) not in sys.path:
        sys.path.append(str(path))

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest, NamespaceEngines


class _TinyEmbeddingFunction:
    _name = "kogwistar-llm-wiki-tests-embedding"

    def name(self) -> str:
        return self._name

    def __call__(self, values):
        vectors = []
        for value in values:
            text = str(value or "")
            vectors.append([float(len(text) + 1), float(sum(ord(ch) for ch in text) % 97 + 1)])
        return vectors


def _build_engine(tmp_path: Path, *, kind: str) -> GraphKnowledgeEngine:
    persist_directory = tmp_path / kind
    persist_directory.mkdir(parents=True, exist_ok=True)
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kind,
        embedding_function=_TinyEmbeddingFunction(),
        backend_factory=build_in_memory_backend,
        namespace=kind,
    )


@pytest.fixture()
def namespace_engines():
    root = ROOT / "tests" / "_engine_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return NamespaceEngines(
        conversation=_build_engine(root, kind="conversation"),
        workflow=_build_engine(root, kind="workflow"),
        kg=_build_engine(root, kind="knowledge"),
        wisdom=_build_engine(root, kind="wisdom"),
    )


@pytest.fixture()
def pipeline(namespace_engines):
    return IngestPipeline(namespace_engines)


@pytest.fixture()
def ingest_request():
    return IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days. Either party may terminate with notice.",
    )


def _normalize_pg_dsn(connection_url: str) -> str:
    try:
        from sqlalchemy.engine import make_url
    except Exception:
        return connection_url
    url = make_url(connection_url)
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _start_longrun_pgvector_container(container_cls, image: str):
    container = None
    try:
        container = container_cls(image)
        container.start()
        return container
    except Exception:
        if container is not None:
            try:
                container.stop()
            except Exception:
                logger.exception(
                    "Failed to stop partially started longrun pgvector test container image=%s",
                    image,
                )
        raise


@pytest.fixture(scope="session", autouse=True)
def _longrun_pgvector_testcontainer():
    backend = os.getenv("KOGWISTAR_LONGRUN_BACKEND", "").strip().lower()
    if backend != "pgvector":
        yield
        return

    pg_source = os.getenv("KOGWISTAR_LONGRUN_PG_SOURCE", "testcontainer").strip().lower() or "testcontainer"
    if pg_source not in {"testcontainer", "custom"}:
        raise ValueError(
            "KOGWISTAR_LONGRUN_PG_SOURCE must be one of {'testcontainer', 'custom'}; "
            f"got {pg_source!r}"
        )

    if pg_source == "custom":
        yield
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except Exception as exc:  # pragma: no cover - optional dependency
        pytest.skip(f"pgvector long-run probe requires testcontainers[postgresql]: {exc}")

    image = os.getenv("KOGWISTAR_LONGRUN_PG_IMAGE", "pgvector/pgvector:pg17")
    logger.info("Starting longrun pgvector test container image=%s", image)
    try:
        container = _start_longrun_pgvector_container(PostgresContainer, image)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Failed to start longrun pgvector test container image={image}: {exc}")

    dsn = _normalize_pg_dsn(container.get_connection_url())
    os.environ["KOGWISTAR_LONGRUN_DSN"] = dsn
    os.environ["KOGWISTAR_LLM_WIKI_TEST_PG_DSN"] = dsn
    try:
        yield
    finally:
        logger.info("Stopping longrun pgvector test container image=%s", image)
        try:
            container.stop()
        except Exception:
            logger.exception("Failed to stop longrun pgvector test container image=%s", image)
