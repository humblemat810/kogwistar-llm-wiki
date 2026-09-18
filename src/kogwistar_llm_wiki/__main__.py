"""CLI entry point: ``python -m kogwistar_llm_wiki``

Sub-commands
------------
demo --workspace <id> --source <path> --vault <path> [--title <text>]
    Run the ephemeral end-to-end demo in one process. Uses the in-memory
    engine bundle, writes the same source and base-knowledge graph spaces as
    normal ingest, then renders the Obsidian vault from explicit graph-space
    reads before exiting.

ingest --workspace <id> --source <path> [--title <text>] [--promotion-mode <mode>]
    Read a source document and populate the workspace state.

report --workspace <id> --data-dir <path> [--backend <name>] [--dsn <dsn>]
    Inspect the persisted wiki graph, summarize quality signals, and emit a
    small node/edge sample sink for review.
    Use --report-scope to choose llm-wiki, maintenance, thinking, or all.
    Use --dump-mode raw or both when you want raw Kogwistar payloads too.

daemon projection --workspace <id> --vault <path> [--interval <s>]
    Run the Obsidian projection daemon (blocking).

daemon maintenance --workspace <id> [--interval <s>]
    Run the maintenance distillation daemon (blocking).

codex-memory --workspace <id> --project-root <path>
    Validate or create a project-to-workspace memory binding and print safe
    Codex MCP configuration instructions. Place global options such as
    --data-dir and --backend before this command.

``demo`` is intentionally single-process and ephemeral so it does not depend on
any process-shared local backend.

The persistent commands expect ``--data-dir`` or ``KOGWISTAR_DATA_DIR`` to
point at a directory containing the local backend state. ``--data-dir`` wins
when both are provided. Use ``--backend postgres`` and ``--dsn`` to switch to a
PostgreSQL-backed store.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .cli.archive_commands import (
    archive_catalog as _archive_catalog_command,
)
from .cli.archive_commands import (
    archive_create as _archive_create_command,
)
from .cli.archive_commands import (
    archive_inspect as _archive_inspect_command,
)
from .cli.archive_commands import (
    archive_restore as _archive_restore_command,
)
from .cli.archive_commands import (
    archive_verify as _archive_verify_command,
)
from .cli.argument_parser import build_argument_parser
from .cli.compose_commands import (
    compose_check as _compose_check_command,
)
from .cli.compose_commands import (
    compose_generate as _compose_generate_command,
)
from .cli.content_commands import (
    read_demo_requests_from_source,
    read_request_from_source,
)
from .cli.content_commands import (
    run_demo as _run_demo_command,
)
from .cli.content_commands import (
    run_ingest as _run_ingest_command,
)
from .cli.content_commands import (
    run_report as _run_report_command,
)
from .cli.embedding_commands import (
    embeddings_adopt_legacy as _embeddings_adopt_legacy_command,
)
from .cli.embedding_commands import (
    embeddings_inspect as _embeddings_inspect_command,
)
from .cli.entrypoint_support import build_demo_engines as _build_demo_engines_impl
from .cli.entrypoint_support import build_engines as _build_engines_impl
from .cli.entrypoint_support import close_engines as _close_engines_impl
from .cli.entrypoint_support import (
    conversation_persistence_kwargs as _conversation_persistence_kwargs_impl,
)
from .cli.entrypoint_support import load_env_file as _load_env_file_impl
from .cli.server_commands import (
    daemon_maintenance as _daemon_maintenance_command,
)
from .cli.server_commands import (
    daemon_projection as _daemon_projection_command,
)
from .cli.server_commands import (
    maintenance_control as _maintenance_control_command,
)
from .cli.server_commands import (
    mcp as _mcp_command,
)
from .cli.server_commands import (
    serve_combined as _serve_combined_command,
)
from .cli.server_commands import (
    workbench as _workbench_command,
)
from .codex.cli_commands import (
    codex_bridge as _codex_bridge_command,
)
from .codex.cli_commands import (
    codex_compose as _codex_compose_command,
)
from .codex.cli_commands import (
    codex_memory as _codex_memory_command,
)
from .codex.cli_commands import (
    seed_bundle as _seed_bundle_command,
)
from .compose.options import ComposeOptions

if TYPE_CHECKING:
    from kogwistar_llm_wiki.models import IngestPipelineRequest, NamespaceEngines

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("kogwistar_llm_wiki")


def _load_env_file(path: Path) -> None:
    return _load_env_file_impl(path)


def _conversation_persistence_kwargs(args: argparse.Namespace) -> dict[str, str]:
    return _conversation_persistence_kwargs_impl(args)


def _close_engines(engines: NamespaceEngines) -> None:
    return _close_engines_impl(engines)


def _read_request_from_source(
    args: argparse.Namespace,
) -> tuple[Path, IngestPipelineRequest]:
    """Compatibility wrapper for the historical CLI helper."""
    return read_request_from_source(args)


def _read_demo_requests_from_source(
    args: argparse.Namespace,
) -> list[tuple[Path, IngestPipelineRequest]]:
    """Compatibility wrapper for the historical CLI helper."""
    return read_demo_requests_from_source(args)


def _build_engines(
    workspace_id: str,
    data_dir: str | None,
    backend: str,
    dsn: str | None,
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: str = "single_stage",
    embedding_profile_mode: str = "enforce",
) -> NamespaceEngines:
    return _build_engines_impl(
        workspace_id,
        data_dir,
        backend,
        dsn,
        split_derived_knowledge=split_derived_knowledge,
        conversation_persistence_mode=conversation_persistence_mode,
        embedding_profile_mode=embedding_profile_mode,
    )


def _build_demo_engines(
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: str = "single_stage",
) -> NamespaceEngines:
    return _build_demo_engines_impl(
        split_derived_knowledge=split_derived_knowledge,
        conversation_persistence_mode=conversation_persistence_mode,
    )


def _cmd_demo(args: argparse.Namespace) -> None:
    return _run_demo_command(
        args,
        build_demo_engines=_build_demo_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_ingest(args: argparse.Namespace) -> None:
    return _run_ingest_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_report(args: argparse.Namespace) -> None:
    return _run_report_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_daemon_projection(args: argparse.Namespace) -> None:
    return _daemon_projection_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_daemon_maintenance(args: argparse.Namespace) -> None:
    return _daemon_maintenance_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_maintenance_control(args: argparse.Namespace) -> None:
    return _maintenance_control_command(args)


def _cmd_serve(args: argparse.Namespace) -> None:
    return _serve_combined_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
    )


def _cmd_workbench(args: argparse.Namespace) -> None:
    return _workbench_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_mcp(args: argparse.Namespace) -> None:
    return _mcp_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_embedding_service(args: argparse.Namespace) -> None:
    """Run the optional isolated multimodal Embedding Service."""
    from llm_wiki_embedding_service.__main__ import main as run_service

    del args
    run_service()


def _cmd_codex_memory(args: argparse.Namespace) -> None:
    return _codex_memory_command(args, build_engines=_build_engines, close_engines=_close_engines)


def _cmd_codex_compose(args: argparse.Namespace) -> None:
    return _codex_compose_command(args)


def _cmd_seed_bundle(args: argparse.Namespace) -> None:
    return _seed_bundle_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
        persistence_kwargs=_conversation_persistence_kwargs,
    )


def _cmd_codex_bridge(args: argparse.Namespace) -> None:
    return _codex_bridge_command(args, load_env_file=_load_env_file)


def _cmd_archive_create(args: argparse.Namespace) -> None:
    return _archive_create_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
    )


def _cmd_archive_inspect(args: argparse.Namespace) -> None:
    return _archive_inspect_command(args)


def _cmd_archive_verify(args: argparse.Namespace) -> None:
    return _archive_verify_command(args)


def _cmd_archive_restore(args: argparse.Namespace) -> None:
    return _archive_restore_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
    )


def _cmd_archive_catalog(args: argparse.Namespace) -> None:
    return _archive_catalog_command(args)


def _cmd_embeddings_inspect(args: argparse.Namespace) -> None:
    return _embeddings_inspect_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
    )


def _cmd_embeddings_adopt_legacy(args: argparse.Namespace) -> None:
    return _embeddings_adopt_legacy_command(
        args,
        build_engines=_build_engines,
        close_engines=_close_engines,
    )


def _compose_options_from_args(args: argparse.Namespace) -> ComposeOptions:
    from .cli.compose_commands import compose_options_from_args

    return compose_options_from_args(args)


def _cmd_compose_generate(args: argparse.Namespace) -> None:
    return _compose_generate_command(args)


def _cmd_compose_check(args: argparse.Namespace) -> None:
    return _compose_check_command(args)


def _command_handlers() -> dict[str, Callable[[argparse.Namespace], object]]:
    return {
        "demo": _cmd_demo,
        "ingest": _cmd_ingest,
        "report": _cmd_report,
        "workbench": _cmd_workbench,
        "mcp": _cmd_mcp,
        "serve": _cmd_serve,
        "embedding_service": _cmd_embedding_service,
        "seed_bundle": _cmd_seed_bundle,
        "archive_create": _cmd_archive_create,
        "archive_inspect": _cmd_archive_inspect,
        "archive_verify": _cmd_archive_verify,
        "archive_restore": _cmd_archive_restore,
        "archive_catalog": _cmd_archive_catalog,
        "embeddings_inspect": _cmd_embeddings_inspect,
        "embeddings_adopt_legacy": _cmd_embeddings_adopt_legacy,
        "codex_memory": _cmd_codex_memory,
        "codex_bridge": _cmd_codex_bridge,
        "codex_compose": _cmd_codex_compose,
        "compose_generate": _cmd_compose_generate,
        "compose_check": _cmd_compose_check,
        "daemon_projection": _cmd_daemon_projection,
        "daemon_maintenance": _cmd_daemon_maintenance,
        "maintenance_control": _cmd_maintenance_control,
    }

def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser(_command_handlers())
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
