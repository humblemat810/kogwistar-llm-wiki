"""CLI entry point: ``python -m kogwistar_llm_wiki``

Sub-commands
------------
daemon projection --workspace <id> --vault <path> [--interval <s>]
    Run the Obsidian projection daemon (blocking).

daemon maintenance --workspace <id> [--interval <s>]
    Run the maintenance distillation daemon (blocking).

Both commands expect KOGWISTAR_DATA_DIR (or --data-dir) to point at a
directory containing the SQLite meta-store and any persistent backend state.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("kogwistar_llm_wiki")


def _build_engines(workspace_id: str, data_dir: str | None):
    """Construct a NamespaceEngines bundle from the given data directory."""
    from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines

    return build_in_memory_namespace_engines(base_dir=data_dir or None)


def _cmd_daemon_projection(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.daemon import ProjectionDaemon

    engines = _build_engines(args.workspace, args.data_dir)
    daemon = ProjectionDaemon(
        engines=engines,
        workspace_id=args.workspace,
        vault_root=args.vault,
        poll_interval=args.interval,
    )

    def _stop(sig, frame):  # noqa: ANN001
        logger.info("Received signal %s — stopping ProjectionDaemon…", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    daemon.run()


def _cmd_daemon_maintenance(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.daemon import MaintenanceDaemon

    engines = _build_engines(args.workspace, args.data_dir)
    daemon = MaintenanceDaemon(
        engines=engines,
        workspace_id=args.workspace,
        poll_interval=args.interval,
    )

    def _stop(sig, frame):  # noqa: ANN001
        logger.info("Received signal %s — stopping MaintenanceDaemon…", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    daemon.run()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m kogwistar_llm_wiki",
        description="LLM-Wiki background worker CLI",
    )
    parser.add_argument("--data-dir", default=None, help="Path to persistent data directory")

    sub = parser.add_subparsers(dest="command", required=True)

    # daemon sub-command
    daemon_p = sub.add_parser("daemon", help="Run a background daemon")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_type", required=True)

    proj_p = daemon_sub.add_parser("projection", help="Obsidian projection daemon")
    proj_p.add_argument("--workspace", required=True, help="Workspace ID")
    proj_p.add_argument("--vault", required=True, help="Obsidian vault root path")
    proj_p.add_argument("--interval", type=float, default=5.0, help="Poll interval (seconds)")
    proj_p.set_defaults(func=_cmd_daemon_projection)

    maint_p = daemon_sub.add_parser("maintenance", help="Maintenance distillation daemon")
    maint_p.add_argument("--workspace", required=True, help="Workspace ID")
    maint_p.add_argument("--interval", type=float, default=10.0, help="Poll interval (seconds)")
    maint_p.set_defaults(func=_cmd_daemon_maintenance)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
