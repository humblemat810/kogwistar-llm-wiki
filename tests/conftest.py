from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
KOGWISTAR_ROOT = ROOT / "kogwistar"
OBSIDIAN_SINK_ROOT = ROOT / "kogwistar-obsidian-sink"
TEST_TMP = ROOT / "tests" / "_tmp"

TEST_TMP.mkdir(parents=True, exist_ok=True)
for key in ("TMPDIR", "TEMP", "TMP"):
    os.environ[key] = str(TEST_TMP)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
for path in (OBSIDIAN_SINK_ROOT,):
    if str(path) not in sys.path:
        sys.path.append(str(path))

import pytest

from kogwistar.id_provider import stable_id
from tests._helpers.pytest_markers import mark_default_ci_items


logger = logging.getLogger(__name__)
LONGRUN_SKIP_LOG_PATH = TEST_TMP / "longrun-skip.log"


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
    "pgvector-persistent": {
        "KOGWISTAR_LLM_WIKI_LONGRUN": "1",
        "KOGWISTAR_LONGRUN_MODE": "fresh",
        "KOGWISTAR_LONGRUN_BACKEND": "pgvector",
        "KOGWISTAR_LONGRUN_PG_SOURCE": "persistent",
        "KOGWISTAR_LONGRUN_PG_DATABASE_MODE": "fingerprint",
        "KOGWISTAR_LONGRUN_PARSER": "page_index",
        "KOGWISTAR_LONGRUN_RESUME_PROBE": "0",
        "KOGWISTAR_LONGRUN_DOC_COUNT": "1",
        "KOGWISTAR_LONGRUN_ALLOW_SMALL": "1",
        "KOGWISTAR_LONGRUN_DOC_PROFILE": "small",
        "KOGWISTAR_LONGRUN_SKIP_MAINTENANCE_INVARIANT": "1",
        "KOGWISTAR_LONGRUN_RUN_DIR": str(ROOT / "tests" / "_tmp" / "longrun-vscode-pgvector-persistent-probe"),
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


def _append_longrun_skip_notice(reason: str) -> None:
    notice = f"[longrun.skip] {reason}"
    print(notice, file=sys.stderr, flush=True)
    LONGRUN_SKIP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LONGRUN_SKIP_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{notice}\n")


def _env_truthy(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configure_longrun_testcontainers_ryuk_env() -> bool:
    """Return whether Ryuk is effectively disabled for long-run pgvector containers."""
    if _env_truthy("GKE_TEST_PG_DISABLE_RYUK"):
        os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"
        return True
    return _env_truthy("TESTCONTAINERS_RYUK_DISABLED")


def _purge_testcontainers_modules() -> None:
    for name in list(sys.modules):
        if name == "testcontainers" or name.startswith("testcontainers."):
            sys.modules.pop(name, None)


def _is_ryuk_port_mapping_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return "port mapping" in text and "8080" in text and "not available" in text


def _load_longrun_postgres_container_cls():
    _configure_longrun_testcontainers_ryuk_env()
    from testcontainers.postgres import PostgresContainer

    return PostgresContainer


def _longrun_persistent_pgvector_container_name() -> str:
    return os.getenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_CONTAINER_NAME", "kogwistar-llm-wiki-pgvector-dev").strip()


def _longrun_persistent_pgvector_host() -> str:
    return os.getenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _longrun_persistent_pgvector_port() -> int:
    raw = os.getenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_PORT", "35432").strip() or "35432"
    port = int(raw)
    if port <= 0 or port > 65535:
        raise ValueError("KOGWISTAR_LONGRUN_PERSISTENT_PG_PORT must be between 1 and 65535")
    return port


def _longrun_persistent_pgvector_user() -> str:
    return os.getenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_USER", "postgres").strip() or "postgres"


def _longrun_persistent_pgvector_password() -> str:
    return os.getenv("KOGWISTAR_LONGRUN_PERSISTENT_PG_PASSWORD", "postgres").strip() or "postgres"


def _run_longrun_docker_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _ensure_longrun_persistent_pgvector_container(image: str) -> str:
    name = _longrun_persistent_pgvector_container_name()
    host = _longrun_persistent_pgvector_host()
    port = _longrun_persistent_pgvector_port()
    user = _longrun_persistent_pgvector_user()
    password = _longrun_persistent_pgvector_password()

    inspect = _run_longrun_docker_command(
        ["container", "inspect", name, "--format", "{{.State.Status}}"]
    )
    if inspect.returncode == 0:
        status = inspect.stdout.strip().lower()
        if status == "paused":
            resumed = _run_longrun_docker_command(["unpause", name])
            if resumed.returncode != 0:
                raise RuntimeError(
                    f"failed to unpause persistent pgvector container {name}: {resumed.stderr.strip() or resumed.stdout.strip()}"
                )
        elif status != "running":
            started = _run_longrun_docker_command(["start", name])
            if started.returncode != 0:
                raise RuntimeError(
                    f"failed to start persistent pgvector container {name}: {started.stderr.strip() or started.stdout.strip()}"
                )
        return (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
        )

    launched = _run_longrun_docker_command(
        [
            "run",
            "-d",
            "--name",
            name,
            "-e",
            f"POSTGRES_USER={user}",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=postgres",
            "-p",
            f"{port}:5432",
            image,
        ]
    )
    if launched.returncode != 0:
        raise RuntimeError(
            f"failed to create persistent pgvector container {name}: {launched.stderr.strip() or launched.stdout.strip()}"
        )
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"


def _ensure_pgvector_database_with_retry(
    dsn: str,
    database_name: str,
    *,
    ready_timeout_seconds: float = 45.0,
    retry_interval_seconds: float = 1.0,
) -> str:
    deadline = time.monotonic() + ready_timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _ensure_pgvector_database(dsn, database_name)
        except Exception as exc:  # pragma: no cover - environment-dependent
            last_error = exc
            time.sleep(retry_interval_seconds)
    if last_error is not None:
        raise last_error
    return _ensure_pgvector_database(dsn, database_name)


def _prepare_longrun_pgvector_database_with_retry(
    dsn: str,
    database_name: str,
    *,
    pg_source: str,
    ready_timeout_seconds: float = 45.0,
    retry_interval_seconds: float = 1.0,
) -> str:
    deadline = time.monotonic() + ready_timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _prepare_longrun_pgvector_database(
                dsn,
                database_name,
                pg_source=pg_source,
            )
        except Exception as exc:  # pragma: no cover - environment-dependent
            last_error = exc
            time.sleep(retry_interval_seconds)
    if last_error is not None:
        raise last_error
    return _prepare_longrun_pgvector_database(dsn, database_name, pg_source=pg_source)


def _longrun_requested(config: pytest.Config) -> bool:
    return bool(
        config.getoption("kogwistar_longrun", default=False)
        or config.getoption("kogwistar_longrun_probe", default=None)
        or os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1"
    )


def _mark_disabled_longrun_items(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _longrun_requested(config):
        return
    reason = "pass --kogwistar-longrun, --kogwistar-longrun-probe, or set KOGWISTAR_LLM_WIKI_LONGRUN=1"
    skip_longrun = pytest.mark.skip(reason=reason)
    emitted = False
    for item in items:
        if "longrun" in item.keywords:
            if not emitted:
                _append_longrun_skip_notice(reason)
                emitted = True
            item.add_marker(skip_longrun)


def pytest_addoption(parser):
    parser.addoption(
        "--kogwistar-longrun",
        action="store_true",
        default=False,
        help="Enable opt-in Kogwistar llm-wiki long-run workflow tests.",
    )
    parser.addoption(
        "--kogwistar-longrun-probe",
        action="store",
        default=None,
        choices=sorted(_LONGRUN_PROBE_ENVS),
        help="Materialize a long-run probe environment from pytest args when VS Code drops launch env.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    mark_default_ci_items(items)
    _mark_disabled_longrun_items(config, items)


def pytest_configure(config):
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    for key in ("TMPDIR", "TEMP", "TMP"):
        os.environ[key] = str(TEST_TMP)
    if config.getoption("kogwistar_longrun", default=False):
        os.environ["KOGWISTAR_LLM_WIKI_LONGRUN"] = "1"
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
        os.getenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint"),
        os.getenv("KOGWISTAR_LONGRUN_CORPUS_PROFILE", "watershed_stress"),
        os.getenv("KOGWISTAR_LONGRUN_PARSER", "workflow_layered"),
        os.getenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20"),
        os.getenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "medium"),
        os.getenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", os.getenv("KOGWISTAR_PARSER_PROVIDER", "ollama")),
        os.getenv("KOGWISTAR_LONGRUN_PARSER_MODEL", os.getenv("KOGWISTAR_PARSER_MODEL", "gemma4:e2b")),
        os.getenv("KOGWISTAR_LONGRUN_PARSER_PROPOSAL_MODE", "children"),
        os.getenv("KOGWISTAR_LONGRUN_TOKEN_MIN", "500"),
        os.getenv("KOGWISTAR_LONGRUN_TOKEN_MAX", "2000"),
        os.getenv("KOGWISTAR_LONGRUN_SKIP_MAINTENANCE_INVARIANT", "0"),
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


def _reset_pgvector_database(dsn: str, database_name: str) -> str:
    """Drop and recreate one harness-owned development database.

    This is intentionally separate from ``_ensure_pgvector_database`` so that
    continuation and shared modes cannot accidentally become destructive.
    The caller must already have restricted this operation to a managed
    testcontainer/persistent-dev-container and fingerprint mode.
    """
    try:
        import sqlalchemy as sa
    except Exception:
        return dsn

    url = sa.engine.make_url(dsn)
    database_name = _normalize_pg_identifier(database_name)
    if database_name in {"postgres", "template0", "template1"}:
        raise ValueError(f"refusing to reset protected PostgreSQL database {database_name!r}")

    admin_url = url.set(drivername="postgresql+psycopg", database="postgres")
    engine = sa.create_engine(
        admin_url.render_as_string(hide_password=False),
        future=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database_name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            conn.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    finally:
        try:
            engine.dispose()
        except Exception:
            logger.exception("Failed to dispose pgvector admin engine after resetting database %s", database_name)
    return url.set(drivername="postgresql+psycopg", database=database_name).render_as_string(hide_password=False)


def _prepare_longrun_pgvector_database(
    dsn: str,
    database_name: str,
    *,
    pg_source: str,
) -> str:
    """Apply the non-destructive/ destructive policy for a long-run database."""
    mode = os.getenv("KOGWISTAR_LONGRUN_MODE", "auto").strip().lower() or "auto"
    database_mode = _longrun_pgvector_database_mode()
    if mode == "fresh" and database_mode == "fingerprint" and pg_source in {"testcontainer", "persistent"}:
        logger.warning(
            "Resetting dev pgvector experiment database for fresh run: database=%s source=%s",
            database_name,
            pg_source,
        )
        return _reset_pgvector_database(dsn, database_name)
    return _ensure_pgvector_database(dsn, database_name)


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
def _longrun_pgvector_testcontainer(pytestconfig: pytest.Config):
    if not _longrun_requested(pytestconfig):
        yield
        return

    backend = os.getenv("KOGWISTAR_LONGRUN_BACKEND", "").strip().lower()
    if backend != "pgvector":
        yield
        return

    pg_source = os.getenv("KOGWISTAR_LONGRUN_PG_SOURCE", "testcontainer").strip().lower() or "testcontainer"
    if pg_source not in {"testcontainer", "custom", "persistent"}:
        raise ValueError(
            "KOGWISTAR_LONGRUN_PG_SOURCE must be one of {'testcontainer', 'custom', 'persistent'}; "
            f"got {pg_source!r}"
        )

    if pg_source == "custom":
        yield
        return

    runtime_env_keys = (
        "KOGWISTAR_LONGRUN_DSN",
        "KOGWISTAR_LLM_WIKI_TEST_PG_DSN",
        "KOGWISTAR_LONGRUN_PG_DATABASE_NAME",
        "TESTCONTAINERS_RYUK_DISABLED",
    )
    original_runtime_env = {
        key: os.environ.get(key) for key in runtime_env_keys
    }

    def restore_runtime_env() -> None:
        for key, value in original_runtime_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    if pg_source == "persistent":
        image = os.getenv("KOGWISTAR_LONGRUN_PG_IMAGE", "pgvector/pgvector:pg17")
        try:
            dsn = _ensure_longrun_persistent_pgvector_container(image)
            dsn = _normalize_pg_dsn(dsn)
            database_name = _longrun_pgvector_database_name()
            dsn = _prepare_longrun_pgvector_database_with_retry(
                dsn,
                database_name,
                pg_source="persistent",
            )
        except Exception as exc:  # pragma: no cover - environment-dependent
            restore_runtime_env()
            reason = f"Failed to prepare persistent longrun pgvector container: {exc}"
            _append_longrun_skip_notice(reason)
            pytest.skip(reason)
        logger.info(
            "Using persistent longrun pgvector container name=%s host=%s port=%s database=%s mode=%s",
            _longrun_persistent_pgvector_container_name(),
            _longrun_persistent_pgvector_host(),
            _longrun_persistent_pgvector_port(),
            database_name,
            _longrun_pgvector_database_mode(),
        )
        os.environ["KOGWISTAR_LONGRUN_DSN"] = dsn
        os.environ["KOGWISTAR_LLM_WIKI_TEST_PG_DSN"] = dsn
        os.environ["KOGWISTAR_LONGRUN_PG_DATABASE_NAME"] = database_name
        try:
            yield
        finally:
            restore_runtime_env()
        return

    try:
        PostgresContainer = _load_longrun_postgres_container_cls()
    except Exception as exc:  # pragma: no cover - optional dependency
        restore_runtime_env()
        reason = f"pgvector long-run probe requires testcontainers[postgresql]: {exc}"
        _append_longrun_skip_notice(reason)
        pytest.skip(reason)

    image = os.getenv("KOGWISTAR_LONGRUN_PG_IMAGE", "pgvector/pgvector:pg17")
    logger.info("Starting longrun pgvector test container image=%s", image)
    initial_ryuk_disabled = _configure_longrun_testcontainers_ryuk_env()
    try:
        container = _start_longrun_pgvector_container(PostgresContainer, image)
    except Exception as exc:  # pragma: no cover - environment-dependent
        if (not initial_ryuk_disabled) and _is_ryuk_port_mapping_failure(exc):
            logger.warning(
                "Failed to start longrun pgvector test container with Ryuk enabled; retrying once without Ryuk. image=%s err=%s",
                image,
                exc,
            )
            os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"
            _purge_testcontainers_modules()
            try:
                PostgresContainer = _load_longrun_postgres_container_cls()
                container = _start_longrun_pgvector_container(PostgresContainer, image)
            except Exception as retry_exc:  # pragma: no cover - environment-dependent
                restore_runtime_env()
                reason = (
                    f"Failed to start longrun pgvector test container image={image} "
                    f"after retry without Ryuk: {retry_exc}"
                )
                _append_longrun_skip_notice(reason)
                pytest.skip(reason)
        else:
            restore_runtime_env()
            reason = f"Failed to start longrun pgvector test container image={image}: {exc}"
            _append_longrun_skip_notice(reason)
            pytest.skip(reason)

    dsn = _normalize_pg_dsn(container.get_connection_url())
    database_name = _longrun_pgvector_database_name()
    dsn = _prepare_longrun_pgvector_database_with_retry(
        dsn,
        database_name,
        pg_source="testcontainer",
    )
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
        finally:
            restore_runtime_env()
