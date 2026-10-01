"""Server, daemon, and local maintenance-control CLI commands."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import threading
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path

from ..models import NamespaceEngines

logger = logging.getLogger("kogwistar_llm_wiki")

BuildEngines = Callable[..., NamespaceEngines]
CloseEngines = Callable[[NamespaceEngines], None]
PersistenceKwargs = Callable[[argparse.Namespace], dict[str, str]]
NOTIFICATION_SOURCE_ENTRY_POINT_GROUP = "kogwistar_llm_wiki.notification_sources"
_NOTIFICATION_SOURCE_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def _load_notification_source_plugins(
    data_dir: str,
    enabled: str,
    authorize_source: Callable[[str, str, str], bool],
) -> dict[str, object]:
    """Load only explicitly enabled trusted notification source adapters."""

    if not isinstance(enabled, str) or not enabled.strip():
        raise ValueError("at least one notification source plugin must be enabled")
    if not callable(authorize_source):
        raise TypeError("notification source authorization callback is required")
    names = tuple(item.strip() for item in enabled.split(","))
    if any(not _NOTIFICATION_SOURCE_ID.fullmatch(name) for name in names):
        raise ValueError("notification source plugin IDs are invalid")
    if len(set(names)) != len(names):
        raise ValueError("notification source plugin IDs must not contain duplicates")
    reserved = {"feed", "system", "core"}
    if reserved.intersection(names):
        raise ValueError("notification source plugin ID is reserved")

    available = {
        point.name: point
        for point in entry_points(group=NOTIFICATION_SOURCE_ENTRY_POINT_GROUP)
    }
    missing = [name for name in names if name not in available]
    if missing:
        raise ValueError(f"notification source plugin {missing[0]!r} is not installed")

    loaded: dict[str, object] = {}
    for name in names:
        factory = available[name].load()
        if not callable(factory):
            raise TypeError(f"notification source plugin {name!r} is not callable")
        adapter = factory(data_dir=data_dir, authorize_source=authorize_source)
        if not callable(getattr(adapter, "list_sources", None)) or not callable(
            getattr(adapter, "read_window", None)
        ):
            raise TypeError(f"notification source plugin {name!r} has an invalid adapter")
        loaded[name] = adapter
    return loaded


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
    from ..app_contracts.workbench_extensions import load_workbench_extensions
    from ..configuration.resource_authorizer import load_resource_authorizer
    from ..daemon import MaintenanceDaemon
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
    extension_ids = tuple(
        item.strip()
        for item in os.environ.get("LLM_WIKI_WORKBENCH_EXTENSIONS", "").split(",")
        if item.strip()
    )
    api = None
    scan_providers = None
    scan_authorizer = None
    try:
        if extension_ids:
            api = WorkbenchApi(
                IngestPipeline(engines),
                resource_authorizer=load_resource_authorizer(),
            )
            load_workbench_extensions(api, extension_ids)
            scan_providers = api.contact_scan_observation_providers()
            scan_authorizer = api.authorize_contact_stream
        daemon = MaintenanceDaemon(
            engines=engines,
            workspace_id=args.workspace,
            poll_interval=args.interval,
            data_dir=args.data_dir or os.environ.get("KOGWISTAR_DATA_DIR") or ".",
            background_interval=args.background_interval,
            contact_observation_providers=scan_providers,
            contact_stream_authorizer=scan_authorizer,
        )
    except Exception:
        if api is not None:
            api.close()
        close_engines(engines)
        raise

    def _stop(sig: int, _frame: object) -> None:
        logger.info("Received signal %s - graceful stop requested for MaintenanceDaemon", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        daemon.run()
    finally:
        daemon.stop()
        if api is not None:
            api.close()
        close_engines(engines)


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
    from ..agent.gateway import AgentGateway
    from ..agent.mcp_server import build_agent_mcp
    from ..app_contracts.workbench_extensions import load_workbench_extensions
    from ..configuration.resource_authorizer import load_resource_authorizer
    from ..daemon import MaintenanceDaemon
    from ..ingest_pipeline import IngestPipeline
    from ..workbench.workbench_api import WorkbenchApi
    from ..workbench.workbench_http import create_workbench_server

    engines = build_engines(args.workspace, args.data_dir, args.backend, args.dsn)
    pipeline = IngestPipeline(engines)
    api = WorkbenchApi(
        pipeline,
        resource_authorizer=load_resource_authorizer(),
    )
    stop_event = threading.Event()
    extension_ids = tuple(
        item.strip()
        for item in os.environ.get("LLM_WIKI_WORKBENCH_EXTENSIONS", "").split(",")
        if item.strip()
    )
    try:
        extensions = load_workbench_extensions(api, extension_ids)
        server = create_workbench_server(
            api, host=args.host, port=args.port, extensions=extensions
        )
    except Exception:
        api.close()
        close_engines(engines)
        raise
    maintenance = MaintenanceDaemon(
        engines,
        args.workspace,
        poll_interval=args.maintenance_interval,
        data_dir=args.data_dir or os.environ.get("KOGWISTAR_DATA_DIR") or ".",
        background_interval=args.background_interval,
        contact_observation_providers=api.contact_scan_observation_providers(),
        contact_stream_authorizer=api.authorize_contact_stream,
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
    from ..configuration.resource_authorizer import load_resource_authorizer
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
        resource_authorizer=load_resource_authorizer(),
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
    """Serve the full MCP protocol through the official MCP SDK."""
    from ..agent.gateway import AgentGateway
    from ..agent.mcp_server import build_agent_mcp
    from ..configuration.resource_authorizer import load_resource_authorizer
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
        gateway = AgentGateway(
            WorkbenchApi(
                IngestPipeline(engines, **persistence_kwargs(args)),
                resource_authorizer=load_resource_authorizer(),
            )
        )
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
