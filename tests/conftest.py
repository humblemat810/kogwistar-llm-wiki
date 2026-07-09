from __future__ import annotations

import logging
import os
import re
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

from kogwistar.id_provider import stable_id
from tests._helpers.pytest_markers import mark_default_ci_items


logger = logging.getLogger(__name__)


_LONGRUN_PROBE_ENVS: dict[str, dict[str, str]] = {
    "pgvector-testcontainer": {
        "KOGWISTAR_LLM_WIKI_LONGRUN": "1",
        "KOGWISTAR_LONGRUN_MODE": "fresh",
        "KOGWISTAR_LONGRUN_BACKEND": "pgvector",
        "KOGWISTAR_LONGRUN_PG_SOURCE": "testcontainer",
        "KOGWISTAR_LONGRUN_PG_DATABASE_MODE": "fingerprint",
        "KOGWISTAR_LONGRUN_PARSER": "page_index",
        "KOGWISTAR_LONGRUN_RESUME_PROBE": "0",
        "KOGWISTAR_LONGRUN_DOC_COUNT": "1",
        "KOGWISTAR_LONGRUN_ALLOW_SMALL": "1",
        "KOGWISTAR_LONGRUN_DOC_PROFILE": "small",
        "KOGWISTAR_LONGRUN_SKIP_MAINTENANCE_INVARIANT": "1",
        "KOGWISTAR_LONGRUN_RUN_DIR": str(ROOT / "tests" / "_tmp" / "longrun-vscode-pgvector-probe"),
    },
}


def _apply_longrun_probe_env(probe: str | None) -> None:
    if not probe:
        return
    try:
        values = _LONGRUN_PROBE_ENVS[probe]
    except KeyError as exc:
        supported = ", ".join(sorted(_LONGRUN_PROBE_ENVS))
        raise ValueError(f"Unsupported --kogwistar-longrun-probe={probe!r}; expected one of: {supported}") from exc
    os.environ.update(values)


def pytest_addoption(parser):
    parser.addoption(
        "--kogwistar-longrun-probe",
        action="store",
        default=None,
        choices=sorted(_LONGRUN_PROBE_ENVS),
        help="Materialize a long-run probe environment from pytest args when VS Code drops launch env.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    mark_default_ci_items(items)


def pytest_configure(config):
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    for key in ("TMPDIR", "TEMP", "TMP"):
        os.environ[key] = str(TEST_TMP)
    _apply_longrun_probe_env(config.getoption("kogwistar_longrun_probe", default=None))


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


def _normalize_pg_identifier(identifier: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", identifier.strip().lower()).strip("_")
    if not cleaned:
        raise ValueError("pgvector database name must not be empty")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", cleaned):
        raise ValueError(
            "pgvector database name must start with a letter or underscore and contain only "
            "letters, digits, and underscores"
        )
    return cleaned


def _longrun_pgvector_database_mode() -> str:
    mode = os.getenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint").strip().lower() or "fingerprint"
    if mode not in {"fingerprint", "shared"}:
        raise ValueError(
            "KOGWISTAR_LONGRUN_PG_DATABASE_MODE must be one of {'fingerprint', 'shared'}; "
            f"got {mode!r}"
        )
    return mode


def _longrun_pgvector_database_name() -> str:
    mode = _longrun_pgvector_database_mode()
    if mode == "shared":
        return _normalize_pg_identifier(
            os.getenv("KOGWISTAR_LONGRUN_PG_DATABASE_NAME", "kogwistar_longrun_shared")
        )
    fingerprint = stable_id(
        "llm_wiki.longrun.pgvector.database",
        os.getenv("KOGWISTAR_LONGRUN_WORKSPACE_ID", "longrun"),
        os.getenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector"),
        os.getenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "parse_first"),
        os.getenv("KOGWISTAR_LONGRUN_PARSER", "workflow_layered"),
        os.getenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20"),
        os.getenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "medium"),
        os.getenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", os.getenv("KOGWISTAR_PARSER_PROVIDER", "ollama")),
        os.getenv("KOGWISTAR_LONGRUN_PARSER_MODEL", os.getenv("KOGWISTAR_PARSER_MODEL", "gemma4:e2b")),
    )
    return f"kogwistar_lr_{fingerprint.hex[:16]}"


def _ensure_pgvector_database(dsn: str, database_name: str) -> str:
    try:
        import sqlalchemy as sa
    except Exception:
        return dsn

    url = sa.engine.make_url(dsn)
    database_name = _normalize_pg_identifier(database_name)
    admin_url = url.set(drivername="postgresql+psycopg", database="postgres")
    engine = sa.create_engine(
        admin_url.render_as_string(hide_password=False),
        future=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            ).scalar_one_or_none()
            if exists is None:
                conn.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    finally:
        try:
            engine.dispose()
        except Exception:
            logger.exception("Failed to dispose pgvector admin engine while creating database %s", database_name)
    return url.set(drivername="postgresql+psycopg", database=database_name).render_as_string(hide_password=False)


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
    database_name = _longrun_pgvector_database_name()
    dsn = _ensure_pgvector_database(dsn, database_name)
    logger.info(
        "Using longrun pgvector experiment database name=%s mode=%s",
        database_name,
        _longrun_pgvector_database_mode(),
    )
    os.environ["KOGWISTAR_LONGRUN_DSN"] = dsn
    os.environ["KOGWISTAR_LLM_WIKI_TEST_PG_DSN"] = dsn
    os.environ["KOGWISTAR_LONGRUN_PG_DATABASE_NAME"] = database_name
    try:
        yield
    finally:
        logger.info("Stopping longrun pgvector test container image=%s", image)
        try:
            container.stop()
        except Exception:
            logger.exception("Failed to stop longrun pgvector test container image=%s", image)
