from __future__ import annotations

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
