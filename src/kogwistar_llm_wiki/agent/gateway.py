"""Core agent gateway composition over the grouped agent tool mixins."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib import (
    request as urllib_request,  # noqa: F401 - legacy source-fetch test seam
)

from ..models import IngestPipelineRequest
from ..otel import LlmWikiTelemetry
from ..parsing.parse_generation_store import ParseGenerationStore
from ..parsing.parse_session_store import ParseSessionStore
from ..parsing.parse_views import ParseViewResolver, parse_session_id
from ..utils import _temporary_namespace
from ..workbench.workbench_api import WorkbenchApi
from .gateway_protocol import (
    a2a_task as _a2a_task,  # noqa: F401 - legacy protocol seam
)
from .gateway_protocol import (
    answer_text as _answer_text,  # noqa: F401 - legacy protocol seam
)
from .gateway_protocol import count_job_statuses as _count_job_statuses
from .gateway_protocol import (
    jsonrpc_error as _jsonrpc_error,  # noqa: F401 - legacy protocol seam
)
from .gateway_protocol import (
    jsonrpc_result as _jsonrpc_result,  # noqa: F401 - legacy HTTP seam
)
from .gateway_protocol import (
    request_id as _request_id,  # noqa: F401 - legacy protocol seam
)
from .gateway_source import decode_metadata_mapping as _decode_metadata_mapping
from .gateway_source import (
    fetch_source_text as _fetch_source_text,  # noqa: F401 - legacy source-tools seam
)
from .gateway_source import node_json as _node_json
from .gateway_source import redact_source_text as _redact_source_text
from .gateway_source import (
    validate_agent_source_uri as _validate_agent_source_uri,  # noqa: F401 - legacy source-tools seam
)
from .gateway_source import (
    validate_reingest_revision as _validate_reingest_revision,
)
from .gateway_source import (
    validate_supplied_provenance as _validate_supplied_provenance,  # noqa: F401 - legacy source-tools seam
)
from .maintenance_tools import AgentMaintenanceToolsMixin
from .protocol import bounded_lens_arguments as _bounded_lens_arguments
from .protocol import (
    request_payload as _request_payload,
)
from .protocol_adapters import AgentProtocolMixin
from .read_tools import AgentReadToolsMixin
from .source_tools import AgentSourceMixin
from .tool_catalog import AgentToolCatalogMixin


@dataclass(frozen=True, slots=True)
class AgentTurn:
    request_id: str
    model: str
    response: dict[str, object]
    status: str = "completed"


class AgentGateway(
    AgentToolCatalogMixin,
    AgentReadToolsMixin,
    AgentMaintenanceToolsMixin,
    AgentProtocolMixin,
    AgentSourceMixin,
):
    """Normalizes agent protocols without bypassing WorkbenchApi safeguards."""

    def __init__(self, api: WorkbenchApi, *, telemetry: LlmWikiTelemetry | None = None) -> None:
        self.api = api
        self.telemetry = telemetry or LlmWikiTelemetry.from_environment()

    def ingest(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        return self._ingest(arguments, reingest=False)

    def reingest(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        source_uri = str(arguments.get("source_uri") or arguments.get("uri") or "").strip()
        source_document_id = str(arguments.get("source_document_id") or "").strip()
        if not workspace_id or not source_uri and not source_document_id:
            raise ValueError("reingest requires workspace_id and source_uri or source_document_id")
        existing = self.source(
            {
                "workspace_id": workspace_id,
                "source_uri": source_uri,
                "source_document_id": source_document_id,
            }
        )
        if not existing.get("exists"):
            raise ValueError("source does not exist; use ingest for a new source")
        _validate_reingest_revision(existing, arguments.get("provenance"))
        request_arguments = dict(arguments)
        request_arguments["source_uri"] = str(existing.get("source_uri") or source_uri)
        existing_metadata = existing.get("metadata")
        if isinstance(existing_metadata, Mapping):
            # Reingest changes source content, not parser policy, unless the
            # caller explicitly supplies a replacement setting.
            for key in (
                "title",
                "source_format",
                "operation_mode",
                "parser_mode",
                "parser_lane",
                "promotion_mode",
                "provenance_policy",
            ):
                if not str(request_arguments.get(key) or "").strip() and existing_metadata.get(key) is not None:
                    request_arguments[key] = existing_metadata[key]
        return self._ingest(request_arguments, reingest=True)

    def source(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        source_uri = str(arguments.get("source_uri") or arguments.get("uri") or "").strip()
        source_document_id = str(arguments.get("source_document_id") or "").strip()
        if not workspace_id or not source_uri and not source_document_id:
            raise ValueError("source requires workspace_id and source_uri or source_document_id")
        request = self._load_source_request(
            workspace_id=workspace_id,
            source_uri=source_uri,
            source_document_id=source_document_id,
        )
        if request is None:
            return {"exists": False, "workspace_id": workspace_id, "source_uri": source_uri, "source_document_id": source_document_id}
        source_document_id = self.api.pipeline._source_document_id(request)
        ns = self.api.pipeline.namespaces_for(workspace_id)
        source_item = next(
            (item for item in self._source_documents(workspace_id) if str(item["id"]) == source_document_id),
            None,
        )
        if source_item is None:
            return {"exists": False, "workspace_id": workspace_id, "source_uri": source_uri, "source_document_id": source_document_id}
        with _temporary_namespace(self.api.pipeline.engines.kg, ns.source_space):
            revisions = self.api.pipeline.engines.kg.read.get_nodes(
                where={"$and": [{"artifact_kind": "source_revision"}, {"source_document_id": source_document_id}]},
                limit=100,
            )
            readiness = self.api.pipeline.engines.kg.read.get_nodes(
                where={"$and": [{"artifact_kind": "source_readiness"}, {"source_document_id": source_document_id}]},
                limit=100,
            )
        jobs = self._maintenance_jobs(workspace_id, source_document_id=source_document_id)
        metadata = _redact_source_text(dict(source_item["metadata"]))
        if isinstance(metadata, dict):
            metadata["provenance"] = _decode_metadata_mapping(metadata.get("provenance"))
        parse_status = self._parse_status(
            workspace_id=workspace_id,
            source_document_id=source_document_id,
            metadata=metadata,
            request=request,
            maintenance_jobs=jobs,
        )
        latest_revision = max(revisions, key=lambda node: int((getattr(node, "metadata", {}) or {}).get("created_at_ms") or 0), default=None)
        return {
            "exists": True,
            "workspace_id": workspace_id,
            "source_document_id": source_document_id,
            "source_uri": str(metadata.get("source_uri") or request.source_uri),
            "title": str(metadata.get("title") or request.title),
            "source_format": str(metadata.get("source_format") or request.source_format),
            "metadata": metadata,
            "revision": _node_json(latest_revision, redact_source_text=True),
            "revisions": [_node_json(node, redact_source_text=True) for node in revisions],
            "readiness": [_node_json(node, redact_source_text=True) for node in readiness],
            "parse_status": parse_status,
            "maintenance_jobs": jobs,
        }

    def _parse_status(
        self,
        *,
        workspace_id: str,
        source_document_id: str,
        metadata: Mapping[str, object],
        request: IngestPipelineRequest,
        maintenance_jobs: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Expose durable session/view state without exposing raw source bytes."""

        revision_id = str(metadata.get("source_revision_id") or "")
        parser_profile = str(metadata.get("parser_lane") or request.parser_lane)
        session_id = str(metadata.get("parse_session_id") or "")
        if not session_id and revision_id:
            session_id = parse_session_id(
                workspace_id=workspace_id,
                source_document_id=source_document_id,
                source_revision_id=revision_id,
                parser_profile=parser_profile,
            )
        session_store = ParseSessionStore(
            self.api.pipeline.engines.conversation.meta_sqlite,
            workspace_id=workspace_id,
        )
        session_rows = session_store.list_for_source(source_document_id)
        if not session_rows and session_id:
            stored = session_store.get(session_id)
            if stored is not None:
                session_rows = [stored]

        def session_payload(
            stored: tuple[object, list[object], int],
        ) -> dict[str, object]:
            session, frontier, version = stored
            counts: dict[str, int] = {}
            depth_distribution: dict[str, int] = {}
            frontier_items: list[dict[str, object]] = []
            for item in frontier:
                status = getattr(getattr(item, "status", None), "value", "unknown")
                counts[status] = counts.get(status, 0) + 1
                depth = str(item.depth)
                depth_distribution[depth] = depth_distribution.get(depth, 0) + 1
                frontier_items.append(
                    {
                        "frontier_id": item.frontier_id,
                        "depth": item.depth,
                        "ordinal": item.ordinal,
                        "status": status,
                        "attempt": item.attempt,
                        "last_error": item.last_error,
                    }
                )
            parser_state = session.parser_state
            linked_jobs = [
                str(job.get("job_id") or "")
                for job in (maintenance_jobs or [])
                if isinstance(job, Mapping)
                and (
                    str((job.get("payload") or {}).get("parse_session_id") or "")
                    == session.session_id
                    if isinstance(job.get("payload"), Mapping)
                    else False
                )
            ]
            return {
                "session_id": session.session_id,
                "phase": session.phase.value,
                "source_revision_id": session.source_revision_id,
                "revision_document_id": session.revision_document_id,
                "generation_id": session.generation_id,
                "max_depth": session.max_depth,
                "max_frontier_items": session.max_frontier_items,
                "max_parser_calls": session.max_parser_calls,
                "max_region_chars": session.max_region_chars,
                "token_budget": session.token_budget,
                "wall_time_seconds": session.wall_time_seconds,
                "parser_calls": session.parser_calls,
                "failure_reason": session.failure_reason,
                "frontier_counts": counts,
                "frontier_depth_distribution": depth_distribution,
                "frontier_items": frontier_items[:256],
                "frontier_ids": list(session.frontier_ids),
                "consumed_frontier_count": len(session.consumed_frontier_ids),
                "parser": {
                    "profile": str(parser_state.get("parser_profile") or parser_profile),
                    "lane": str(parser_state.get("parser_lane") or ""),
                    "mode": str(parser_state.get("parser_mode") or ""),
                    "provider": str(parser_state.get("llm_provider") or ""),
                    "model": str(parser_state.get("llm_model") or ""),
                },
                "maintenance_job_ids": [job_id for job_id in linked_jobs if job_id],
                "last_progress_at": session.last_progress_at.isoformat(),
                "projection_version": version,
            }

        sessions_payload = [session_payload(row) for row in session_rows]
        generation_rows = ParseGenerationStore(
            self.api.pipeline.engines.conversation.meta_sqlite,
            workspace_id=workspace_id,
        ).list_for_source(source_document_id)
        view = ParseViewResolver(
            self.api.pipeline.engines.conversation.meta_sqlite,
            workspace_id=workspace_id,
        ).resolve(
            source_document_id,
            fallback_revision_document_id=str(metadata.get("revision_document_id") or ""),
        )
        return {
            "session": sessions_payload[0] if sessions_payload else None,
            "sessions": sessions_payload,
            "generations": [
                {
                    "generation_id": generation.generation_id,
                    "source_revision_id": generation.source_revision_id,
                    "revision_document_id": generation.revision_document_id,
                    "parser_profile": generation.parser_profile,
                    "parser_version": generation.parser_version,
                    "status": generation.status.value,
                    "created_at": generation.created_at.isoformat(),
                    "projection_version": version,
                }
                for generation, version in generation_rows
            ],
            "active_view": {
                "view_id": view.view_id,
                "view_version": view.view_version,
                "revision_document_id": view.revision_document_id,
                "generation_ids": list(view.generation_ids),
                "member_ids": list(view.member_ids),
                "is_legacy": view.is_legacy,
            },
        }

    def status(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        if not workspace_id:
            raise ValueError("status requires workspace_id")
        # Resolve these through the compatibility module so existing operator
        # integrations and provider-free tests can still replace the report
        # serializer at the historical import path.
        from .. import agent_gateway as compatibility_module

        report = compatibility_module.build_workspace_quality_report(
            self.api.pipeline.engines,
            workspace_id=workspace_id,
            report_scope="all",
        )
        maintenance_errors: list[str] = []
        jobs = self._maintenance_jobs(workspace_id, errors=maintenance_errors)
        usage_error: str | None = None
        usage_snapshot: dict[str, object] | None = None
        try:
            snapshot = self.api.pipeline.usage_projection(workspace_id).snapshot()
            usage_snapshot = snapshot.as_dict() if snapshot is not None else None
        except Exception as exc:  # noqa: BLE001
            usage_error = f"{type(exc).__name__}: {exc}"
        source_states: dict[str, int] = {}
        for item in self._source_documents(workspace_id):
            state = str(item["metadata"].get("revision_status") or "registered")
            source_states[state] = source_states.get(state, 0) + 1
        return {
            "workspace_id": workspace_id,
            "health": self.api.readiness(),
            "graph": compatibility_module.asdict(report),
            "sources": {"count": len(self._source_documents(workspace_id)), "states": source_states},
            "maintenance": {
                "available": not maintenance_errors,
                "counts": _count_job_statuses(jobs),
                "jobs": jobs[:100],
                "errors": maintenance_errors,
            },
            "usage": {
                "available": usage_error is None,
                "snapshot": usage_snapshot,
                "error": usage_error,
            },
        }

    def propose(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        validation = self.api.validate_proposal(arguments)
        if not validation.get("accepted"):
            return validation
        request = arguments.get("request")
        proposal = arguments.get("proposal")
        if not isinstance(request, Mapping) or not isinstance(proposal, Mapping):
            return validation
        workspace_id = str(request.get("workspace_id") or "").strip()
        if not workspace_id:
            return {**validation, "accepted": False, "reason": "workspace_id_required"}
        interaction = self.api.interactions.persist_proposal(
            workspace_id=workspace_id,
            request=request,
            proposal=proposal,
        )
        return {
            **validation,
            "interaction_id": interaction.interaction_id,
            "status": interaction.status,
            "confirmation_required": True,
        }

    def confirm(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        return self.api.confirm_cockpit_proposal(arguments)

    def _ingest(self, arguments: Mapping[str, Any], *, reingest: bool) -> dict[str, object]:
        request = self._source_request(arguments, allow_existing_revision=reingest)
        if reingest and self._load_source_request(
            workspace_id=request.workspace_id,
            source_uri=request.source_uri,
        ) is None:
            raise ValueError("reingest source identity could not be resolved")
        artifacts = self.api.pipeline.run(request)
        return {"status": "reingested" if reingest else "ingested", "request": request.model_dump(dump_format="json"), "artifacts": asdict(artifacts)}

    def _answer(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request = _request_payload(payload)
        request = _bounded_lens_arguments(request)
        return self.api.ask(request)


__all__ = ["AgentGateway", "AgentTurn"]
