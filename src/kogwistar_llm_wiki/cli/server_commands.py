"""Server, daemon, and local maintenance-control CLI commands."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
from collections.abc import Callable
from pathlib import Path

from ..models import NamespaceEngines

logger = logging.getLogger("kogwistar_llm_wiki")

BuildEngines = Callable[..., NamespaceEngines]
CloseEngines = Callable[[NamespaceEngines], None]
PersistenceKwargs = Callable[[argparse.Namespace], dict[str, str]]


def daemon_projection(
    args: argparse.Namespace,
    *,
    build_engines: BuildEngines,
    close_engines: CloseEngines,
    persistence_kwargs: PersistenceKwargs,
) -> None:
    from ..daemon import ProjectionDaemon

    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
    )
    Path(args.vault).expanduser().resolve().mkdir(parents=True, exist_ok=True)
    daemon = ProjectionDaemon(
        engines=engines,
        workspace_id=args.workspace,
        vault_root=args.vault,
        poll_interval=args.interval,
    )

    def _stop(sig: int, _frame: object) -> None:
        logger.info("Received signal %s - graceful stop requested for ProjectionDaemon", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    daemon.run()


def daemon_maintenance(
    args: argparse.Namespace,
    *,
    build_engines: BuildEngines,
    close_engines: CloseEngines,
    persistence_kwargs: PersistenceKwargs,
) -> None:
    from ..daemon import MaintenanceDaemon

    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
    )
    daemon = MaintenanceDaemon(
        engines=engines,
        workspace_id=args.workspace,
        poll_interval=args.interval,
        data_dir=args.data_dir or os.environ.get("KOGWISTAR_DATA_DIR") or ".",
        background_interval=args.background_interval,
    )

    def _stop(sig: int, _frame: object) -> None:
        logger.info("Received signal %s - graceful stop requested for MaintenanceDaemon", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    daemon.run()


def maintenance_control(args: argparse.Namespace) -> None:
    """Change maintenance modes without opening the database or HTTP API."""
    from ..maintenance.maintenance_control import send_control_command

    if args.status:
        result = send_control_command(
            args.data_dir or os.environ.get("KOGWISTAR_DATA_DIR") or ".",
            status=True,
        )
        print(json.dumps(result, sort_keys=True))
        return

    budget = None
    if args.budget_json:
        try:
            parsed = json.loads(args.budget_json)
        except json.JSONDecodeError as exc:
            raise ValueError("--budget-json must contain a JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("--budget-json must contain a JSON object")
        budget = parsed
    budget_values = {
        window: {
            metric: getattr(args, f"{window}_{metric}")
            for metric in ("tokens", "input_tokens", "output_tokens", "money")
            if getattr(args, f"{window}_{metric}") is not None
        }
        for window in ("daily", "weekly", "monthly")
    }
    if any(budget_values.values()):
        budget = budget_values
    if args.clear_budget:
        budget = {}
    profile_ladder = None
    if args.profile_ladder_json:
        try:
            profile_ladder = json.loads(args.profile_ladder_json)
        except json.JSONDecodeError as exc:
            raise ValueError("--profile-ladder-json must contain a JSON array") from exc
        if not isinstance(profile_ladder, list):
            raise ValueError("--profile-ladder-json must contain a JSON array")

    result = send_control_command(
        args.data_dir or os.environ.get("KOGWISTAR_DATA_DIR") or ".",
        request_enabled=args.request_enabled,
        background_enabled=args.background_enabled,
        enabled=args.enabled,
        profile=args.profile,
        profile_ladder=profile_ladder,
        budget=budget,
        persist=not args.runtime_only,
        actor="llm-wiki-maintenance-control",
    )
    print(json.dumps(result, sort_keys=True))


def serve_combined(
    args: argparse.Namespace,
    *,
    build_engines: BuildEngines,
    close_engines: CloseEngines,
) -> None:
    """Serve REST, MCP, and maintenance from one shared engine bundle."""
    from ..agent.mcp_server import build_agent_mcp
    from ..agent_gateway import AgentGateway
    from ..daemon import MaintenanceDaemon
    from ..ingest_pipeline import IngestPipeline
    from ..workbench.workbench_api import WorkbenchApi
    from ..workbench.workbench_http import create_workbench_server

    engines = build_engines(args.workspace, args.data_dir, args.backend, args.dsn)
    pipeline = IngestPipeline(engines)
    api = WorkbenchApi(pipeline)
    stop_event = threading.Event()
    server = create_workbench_server(api, host=args.host, port=args.port)
    maintenance = MaintenanceDaemon(
        engines,
        args.workspace,
        poll_interval=args.maintenance_interval,
        data_dir=args.data_dir or os.environ.get("KOGWISTAR_DATA_DIR") or ".",
        background_interval=args.background_interval,
    )
    gateway = AgentGateway(api)
    mcp = build_agent_mcp(gateway)
    threads = [
        threading.Thread(target=server.serve_forever, name="llm-wiki-rest", daemon=True),
        threading.Thread(
            target=lambda: mcp.run(
                transport="streamable-http",
                host=args.host,
                port=args.mcp_port,
                path=args.mcp_path,
            ),
            name="llm-wiki-mcp",
            daemon=True,
        ),
        threading.Thread(target=maintenance.run, name="llm-wiki-maintenance", daemon=True),
    ]

    def _stop(_sig: int, _frame: object) -> None:
        stop_event.set()
        maintenance.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        for thread in threads:
            thread.start()
        logger.info(
            "combined_server_started workspace=%s rest=%s mcp=%s",
            args.workspace,
            args.port,
            args.mcp_port,
        )
        while not stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        maintenance.stop()
        server.shutdown()
        server.server_close()
        api.close()
        close_engines(engines)


def workbench(
    args: argparse.Namespace,
    *,
    build_engines: BuildEngines,
    close_engines: CloseEngines,
    persistence_kwargs: PersistenceKwargs,
) -> None:
    from ..codex.codex_workbench_agent import (
        CodexCliCockpitResponder,
        CodexCliSettings,
        HostCockpitResponder,
    )
    from ..ingest_pipeline import IngestPipeline
    from ..workbench.workbench_api import WorkbenchApi
    from ..workbench.workbench_http import serve_workbench

    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
    )

    def trace_line(line: str) -> None:
        logger.info("workbench_cockpit_trace %s", line)

    callback_url = os.environ.get("LLM_WIKI_COCKPIT_CALLBACK_URL", "").strip()
    if callback_url:
        allowed_hosts = os.environ.get(
            "LLM_WIKI_COCKPIT_CALLBACK_ALLOWED_HOSTS",
            "host.docker.internal,localhost,127.0.0.1",
        ).split(",")
        responder = HostCockpitResponder(
            callback_url,
            allowed_hosts=allowed_hosts,
            token=os.environ.get("LLM_WIKI_COCKPIT_CALLBACK_TOKEN"),
            timeout_seconds=max(
                1.0,
                float(
                    os.environ.get(
                        "LLM_WIKI_COCKPIT_CALLBACK_TIMEOUT_SECONDS",
                        args.codex_timeout,
                    )
                ),
            ),
            trace_line=trace_line,
        )
    else:
        responder = CodexCliCockpitResponder(
            CodexCliSettings(
                executable=args.codex_executable,
                model=args.codex_model,
                profile=args.codex_profile,
                timeout_seconds=args.codex_timeout,
                transport=getattr(args, "codex_transport", None)
                or os.environ.get("KOGWISTAR_CODEX_TRANSPORT", "exec"),
            ),
            trace_line=trace_line,
        )
    api = WorkbenchApi(
        IngestPipeline(engines, **persistence_kwargs(args)),
        cockpit_responder=responder,
        codex_worker_count=args.codex_workers,
        trace_sink=lambda event: logger.info(
            "workbench_worker_trace %s", json.dumps(event, sort_keys=True)
        ),
    )
    api.recover_interactions(args.workspace)
    logger.info(
        "workbench_started workspace=%s host=%s port=%s codex_model=%s workers=%s",
        args.workspace,
        args.host,
        args.port,
        args.codex_model or "codex-default",
        args.codex_workers,
    )
    try:
        serve_workbench(api, host=args.host, port=args.port)
    finally:
        close_engines(engines)


def mcp(
    args: argparse.Namespace,
    *,
    build_engines: BuildEngines,
    close_engines: CloseEngines,
    persistence_kwargs: PersistenceKwargs,
) -> None:
    """Serve the full MCP protocol through the optional FastMCP dependency."""
    from ..agent.mcp_server import build_agent_mcp
    from ..agent_gateway import AgentGateway
    from ..ingest_pipeline import IngestPipeline
    from ..workbench.workbench_api import WorkbenchApi

    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
    )
    try:
        gateway = AgentGateway(WorkbenchApi(IngestPipeline(engines, **persistence_kwargs(args))))
        mcp_server = build_agent_mcp(gateway)
        logger.info(
            "agent_mcp_started workspace=%s transport=%s host=%s port=%s",
            args.workspace,
            args.transport,
            args.host,
            args.port,
        )
        run_kwargs: dict[str, object] = {"transport": args.transport}
        if args.transport != "stdio":
            run_kwargs.update({"host": args.host, "port": args.port, "path": args.path})
        mcp_server.run(**run_kwargs)
    finally:
        close_engines(engines)
