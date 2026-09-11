"""One grounded interaction contract behind agent-facing protocols."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
import json
import os
from pathlib import PurePosixPath
import time
import uuid
from collections.abc import Mapping
from numbers import Real
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urlparse

from .otel import LlmWikiTelemetry
from .inspection import build_workspace_quality_report
from .models import IngestPipelineRequest
from .utils import _temporary_namespace
from .workbench_api import WorkbenchApi


@dataclass(frozen=True, slots=True)
class AgentTurn:
    request_id: str
    model: str
    response: dict[str, object]
    status: str = "completed"


class AgentGateway:
    """Normalizes agent protocols without bypassing WorkbenchApi safeguards."""

    def __init__(self, api: WorkbenchApi, *, telemetry: LlmWikiTelemetry | None = None) -> None:
        self.api = api
        self.telemetry = telemetry or LlmWikiTelemetry.from_environment()

    def responses(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request_id = _request_id(payload, "resp")
        model = str(payload.get("model") or "llm-wiki-deterministic")
        with self.telemetry.span("llm_wiki.responses", {"request_id": request_id, "model": model}):
            result = self._answer(payload)
        answer = _answer_text(result)
        return {
            "id": request_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": model,
            "output": [{
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": answer, "annotations": []}],
            }],
            "usage": None,
            "llm_wiki": result,
        }

    def chat_completions(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request_id = _request_id(payload, "chatcmpl")
        model = str(payload.get("model") or "llm-wiki-deterministic")
        with self.telemetry.span("llm_wiki.chat_completions", {"request_id": request_id, "model": model}):
            result = self._answer(payload)
        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": _answer_text(result)},
                "finish_reason": "stop",
            }],
            "usage": None,
            "llm_wiki": result,
        }

    def a2a_message(self, payload: Mapping[str, Any]) -> dict[str, object]:
        """Submit or execute an A2A-style message using durable interactions."""
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        request = _request_payload(payload)
        request.update({str(k): v for k, v in metadata.items()})
        if bool(payload.get("background", True)) and self.api.dispatcher is not None:
            interaction = self.api.submit_interaction(request)
            return _a2a_task(interaction)
        result = self._answer(request)
        task_id = _request_id(payload, "task")
        return {"id": task_id, "status": {"state": "completed"}, "artifacts": [{"parts": [{"kind": "text", "text": _answer_text(result)}]}], "metadata": {"llm_wiki": result}}

    def a2a_task(self, *, workspace_id: str, task_id: str) -> dict[str, object] | None:
        interaction = self.api.get_interaction(workspace_id=workspace_id, interaction_id=task_id)
        if interaction is None:
            return None
        return _a2a_task(interaction)

    def a2a_jsonrpc(self, payload: Mapping[str, Any]) -> dict[str, object]:
        """Handle the A2A JSON-RPC binding without duplicating task logic."""
        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0":
            return _jsonrpc_error(request_id, -32600, "Invalid Request", "jsonrpc must be '2.0'")
        method = payload.get("method")
        params = payload.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, Mapping):
            return _jsonrpc_error(request_id, -32600, "Invalid Request", "method and params are invalid")
        try:
            if method == "message/send":
                return _jsonrpc_result(request_id, _a2a_task(self.a2a_message(params), standard=True))
            if method == "tasks/get":
                metadata = params.get("metadata") if isinstance(params.get("metadata"), Mapping) else {}
                workspace_id = str(params.get("workspace_id") or metadata.get("workspace_id") or "default")
                task_id = str(params.get("id") or params.get("taskId") or "")
                if not task_id:
                    return _jsonrpc_error(request_id, -32602, "Invalid params", "tasks/get requires id")
                task = self.a2a_task(workspace_id=workspace_id, task_id=task_id)
                if task is None:
                    return _jsonrpc_error(request_id, -32001, "Task not found", {"taskId": task_id})
                return _jsonrpc_result(request_id, _a2a_task(task, standard=True))
            if method == "message/stream":
                return _jsonrpc_result(request_id, _a2a_task(self.a2a_message(params), standard=True))
            return _jsonrpc_error(request_id, -32601, "Method not found", method)
        except (KeyError, TypeError, ValueError) as exc:
            return _jsonrpc_error(request_id, -32602, "Invalid params", str(exc))

    def mcp_tool_names(self) -> tuple[str, ...]:
        return (
            "query", "search", "ingest", "source", "reingest", "maintain",
            "status", "hypergraph_search", "history", "propose", "confirm",
        )

    def mcp_tool_descriptions(self) -> dict[str, str]:
        return {
            "query": "Ask a grounded question about existing wiki knowledge.",
            "search": "Retrieve bounded relevant knowledge and supporting context.",
            "ingest": "Capture a raw source through the canonical ingestion pipeline.",
            "source": "Inspect a source using workspace_id plus source_uri or source_document_id.",
            "reingest": "Update an existing source using workspace_id plus source_uri or source_document_id.",
            "maintain": "Queue maintenance using workspace_id plus a topic or source_document_ids.",
            "status": "Report wiki health, source state, graph quality, and maintenance state.",
            "hypergraph_search": "Inspect bounded raw graph and hypergraph structure read-only.",
            "history": "Inspect prior investigations, decisions, and grounding metadata.",
            "propose": "Validate a candidate durable knowledge change without applying it.",
            "confirm": "Explicitly approve and apply a previously validated knowledge change.",
        }

    def call_mcp_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, object]:
        with self.telemetry.span("llm_wiki.mcp_tool", {"tool": name}):
            handlers = {
                "query": self.query,
                "search": self.search,
                "ingest": self.ingest,
                "source": self.source,
                "reingest": self.reingest,
                "maintain": self.maintain,
                "status": self.status,
                "hypergraph_search": self.hypergraph_search,
                "history": self.history,
                "propose": self.propose,
                "confirm": self.confirm,
            }
            handler = handlers.get(name)
            if handler is not None:
                return handler(arguments)
            raise ValueError(f"unknown MCP tool: {name}")

    def query(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        return self._answer(arguments)

    def search(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        return self.api.get_lens(_bounded_lens_arguments(arguments))

    def history(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        return {
            "records": self.api.get_history(
                workspace_id=str(arguments["workspace_id"]),
                session_id=str(arguments.get("session_id") or "") or None,
                limit=min(1000, max(1, int(arguments.get("limit", 100)))),
            )
        }

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
            "maintenance_jobs": jobs,
        }

    def maintain(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        topic = str(arguments.get("topic") or "").strip()
        objective = str(arguments.get("objective") or arguments.get("policy") or "").strip()
        if not workspace_id or not topic and not arguments.get("source_document_ids"):
            raise ValueError("maintain requires workspace_id and topic or source_document_ids")
        budgets = _budgets(arguments)
        raw_source_ids = arguments.get("source_document_ids") or ()
        if not isinstance(raw_source_ids, (list, tuple, set, frozenset)):
            raise ValueError("source_document_ids must be a list of IDs")
        source_ids = [str(value) for value in raw_source_ids if str(value).strip()]
        if not source_ids:
            source_ids = self._source_ids_for_topic(workspace_id, topic)
        source_requests: list[tuple[str, IngestPipelineRequest]] = []
        missing_source_ids: list[str] = []
        for source_id in source_ids:
            request = self._load_source_request(workspace_id=workspace_id, source_document_id=source_id)
            if request is None:
                missing_source_ids.append(source_id)
            else:
                source_requests.append((source_id, request))
        if missing_source_ids and raw_source_ids:
            raise ValueError(
                "source_document_ids do not resolve in workspace: "
                + ", ".join(missing_source_ids)
            )
        jobs: list[str] = []
        skipped_source_ids: list[str] = []
        ns = self.api.pipeline.namespaces_for(workspace_id)
        source_requests, skipped_source_ids = _limit_budgeted_sources(source_requests, budgets)
        for index, (source_id, request) in enumerate(source_requests):
            job_id = self.api.pipeline.create_maintenance_request(
                request=request,
                source_document_id=source_id,
                namespace=ns.conv_bg,
                maintenance_kind=str(arguments.get("maintenance_kind") or "document_propose_crosslinks"),
                objective=objective or None,
                budgets=_partition_budgets(budgets, len(source_requests), index),
            )
            jobs.append(job_id)
        return {
            "workspace_id": workspace_id,
            "topic": topic,
            "objective": objective,
            "status": "queued" if jobs else "no_matching_sources",
            "job_ids": jobs,
            "budgets": budgets,
            "skipped_source_document_ids": skipped_source_ids,
        }

    def status(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        if not workspace_id:
            raise ValueError("status requires workspace_id")
        report = build_workspace_quality_report(self.api.pipeline.engines, workspace_id=workspace_id, report_scope="all")
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
            "graph": asdict(report),
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

    def hypergraph_search(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        payload = _bounded_lens_arguments(arguments)
        payload.setdefault("max_hyperedges", 12)
        payload.setdefault("max_nodes", 40)
        payload.setdefault("max_edges", 80)
        return self.api.get_lens(payload)

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

    def _source_request(
        self,
        arguments: Mapping[str, Any],
        *,
        allow_existing_revision: bool = False,
    ) -> IngestPipelineRequest:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        source_uri = str(arguments.get("source_uri") or arguments.get("uri") or "").strip()
        raw_text = arguments.get("raw_text")
        if not workspace_id or not source_uri:
            raise ValueError("ingest requires workspace_id and source_uri")
        if raw_text is None:
            raw_text = _fetch_source_text(source_uri)
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("ingest requires non-empty raw_text or a fetchable source_uri")
        _validate_agent_source_uri(source_uri)
        policy = str(arguments.get("provenance_policy") or "optional").strip().lower()
        if policy not in {"required", "optional", "disabled"}:
            raise ValueError("provenance_policy must be required, optional, or disabled")
        provenance = arguments.get("provenance")
        if provenance is not None and not isinstance(provenance, Mapping):
            raise ValueError("provenance must be an object when supplied")
        if policy == "required" and not isinstance(provenance, Mapping):
            raise ValueError("required provenance was not supplied")
        if isinstance(provenance, Mapping):
            _validate_supplied_provenance(
                provenance,
                workspace_id=workspace_id,
                source_uri=source_uri,
                raw_text=raw_text,
                require_identity=policy == "required",
            )
            if provenance.get("source_revision_id") and not allow_existing_revision:
                raise ValueError(
                    "provenance source_revision_id cannot be resolved for a new source; use reingest"
                )
        title = str(arguments.get("title") or PurePosixPath(urlparse(source_uri).path).name or source_uri)
        request = IngestPipelineRequest(
            workspace_id=workspace_id,
            source_uri=source_uri,
            title=title,
            raw_text=raw_text,
            source_format=str(arguments.get("source_format") or "text"),
            operation_mode=str(arguments.get("operation_mode") or "parse_first"),
            parser_mode=str(arguments.get("parser_mode") or "heuristic"),
            parser_lane=str(arguments.get("parser_lane") or "page_index"),
            promotion_mode=str(arguments.get("promotion_mode") or "pending"),
            auto_accept_threshold=float(arguments.get("auto_accept_threshold", 0.95)),
            llm_provider=str(arguments.get("llm_provider")) if arguments.get("llm_provider") is not None else None,
            llm_model=str(arguments.get("llm_model")) if arguments.get("llm_model") is not None else None,
            provenance_policy=policy,
            provenance=dict(provenance) if isinstance(provenance, Mapping) else None,
        )
        declared_source_id = str((provenance or {}).get("source_document_id") or "").strip() if isinstance(provenance, Mapping) else ""
        if declared_source_id:
            expected_source_id = self.api.pipeline._source_document_id(request)
            if declared_source_id != expected_source_id:
                raise ValueError("provenance source_document_id does not match request source identity")
        return request

    def _load_source_request(self, *, workspace_id: str, source_uri: str = "", source_document_id: str = "") -> IngestPipelineRequest | None:
        candidates = self._source_documents(workspace_id)
        by_id = next((item for item in candidates if source_document_id and str(item["id"]) == source_document_id), None)
        by_uri = next((item for item in candidates if source_uri and str(item["metadata"].get("source_uri") or "") == source_uri), None)
        if by_id is not None and by_uri is not None and str(by_id["id"]) != str(by_uri["id"]):
            raise ValueError("source_document_id and source_uri identify different sources")
        candidate = by_id or by_uri
        if candidate is None:
            return None
        metadata = dict(candidate["metadata"])
        return IngestPipelineRequest(
            workspace_id=workspace_id,
            source_uri=str(metadata.get("source_uri") or source_uri),
            title=str(metadata.get("title") or candidate["id"]),
            raw_text=str(candidate["content"] or ""),
            source_format=str(metadata.get("source_format") or "text"),
            operation_mode=str(metadata.get("operation_mode") or "parse_first"),
            parser_mode=str(metadata.get("parser_mode") or "heuristic"),
            parser_lane=str(metadata.get("parser_lane") or "page_index"),
            promotion_mode=str(metadata.get("promotion_mode") or "pending"),
            provenance_policy=str(metadata.get("provenance_policy") or "optional"),
            provenance=_decode_metadata_mapping(metadata.get("provenance")),
        )

    def _source_documents(self, workspace_id: str) -> list[dict[str, object]]:
        ns = self.api.pipeline.namespaces_for(workspace_id)
        with _temporary_namespace(self.api.pipeline.engines.kg, ns.source_space):
            # Source discovery must not lose records because revision and
            # readiness artifacts consume an arbitrary fixed page size.
            nodes = self.api.pipeline.engines.kg.read.get_nodes(limit=None)
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for node in nodes:
            metadata = dict(getattr(node, "metadata", {}) or {})
            if metadata.get("graph_space") != "source" and metadata.get("artifact_kind") != "source_revision":
                continue
            source_id = str(metadata.get("source_document_id") or metadata.get("doc_id") or "").strip()
            raw_text = metadata.get("source_raw_text")
            if not isinstance(raw_text, str):
                raw_text = getattr(node, "content", None)
            if not source_id or not isinstance(raw_text, str) or source_id in seen:
                continue
            seen.add(source_id)
            result.append({"id": source_id, "metadata": metadata, "content": raw_text})
        return result

    def _source_ids_for_topic(self, workspace_id: str, topic: str) -> list[str]:
        terms = {term.lower() for term in topic.split() if len(term) > 2}
        matches = []
        for item in self._source_documents(workspace_id):
            haystack = " ".join([str(item["content"]), json.dumps(item["metadata"], sort_keys=True)]).lower()
            if not terms or any(term in haystack for term in terms):
                matches.append(str(item["id"]))
        return matches

    def _maintenance_jobs(
        self,
        workspace_id: str,
        *,
        source_document_id: str | None = None,
        errors: list[str] | None = None,
    ) -> list[dict[str, object]]:
        ns = self.api.pipeline.namespaces_for(workspace_id)
        jobs = []
        for status in ("PENDING", "DOING", "DONE", "FAILED"):
            try:
                rows = self.api.pipeline.engines.conversation.jobs.list(namespace=ns.maintenance_jobs, status=status, limit=10_000)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"{type(exc).__name__}: {exc}")
                rows = []
            for job in rows:
                if source_document_id and str(job.entity_id) != source_document_id:
                    continue
                public_status = "completed" if status == "DONE" else status.lower()
                jobs.append({"job_id": str(job.job_id), "status": public_status, "entity_id": str(job.entity_id), "job_kind": str(job.job_kind), "payload": dict(job.payload)})
        return jobs

    def _answer(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request = _request_payload(payload)
        request = _bounded_lens_arguments(request)
        return self.api.ask(request)


def _request_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        text = next((_content_to_text(item.get("content")) for item in reversed(messages) if isinstance(item, Mapping) and item.get("role") == "user"), "")
    else:
        value = payload.get("input", payload.get("query_text", ""))
        if isinstance(payload.get("message"), Mapping):
            parts = payload["message"].get("parts")
            if isinstance(parts, list):
                value = "\n".join(_content_to_text(part) for part in parts)
        if isinstance(value, list):
            text = "\n".join(_content_to_text(item.get("content", "")) for item in value if isinstance(item, Mapping))
        else:
            text = _content_to_text(value)
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    request: dict[str, object] = {"workspace_id": str(payload.get("workspace_id") or metadata.get("workspace_id") or "default"), "query_text": text, "session_id": str(payload.get("session_id") or metadata.get("session_id") or "default"), "mode": str(payload.get("mode") or metadata.get("mode") or "deterministic")}
    for key in ("graph_spaces", "semantic_retrieval", "explicit_anchor_ids", "hop_limit", "max_nodes", "max_edges", "max_hyperedges", "pinned_node_ids", "source_watermark", "include_tombstones", "interaction_id"):
        if key in payload:
            request[key] = payload[key]
    return request


def _budgets(arguments: Mapping[str, Any]) -> dict[str, object]:
    names = ("max_time_seconds", "max_llm_calls", "max_tokens", "max_cost_usd", "max_steps")
    integer_names = {"max_llm_calls", "max_tokens", "max_steps"}
    result: dict[str, object] = {}
    for name in names:
        if name in arguments and arguments[name] is not None:
            value = arguments[name]
            if isinstance(value, bool) or not isinstance(value, Real) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
            if name in integer_names and not isinstance(value, int):
                raise ValueError(f"{name} must be a non-negative integer")
            result[name] = value
    return result


def _limit_budgeted_sources(
    source_requests: list[tuple[str, IngestPipelineRequest]],
    budgets: Mapping[str, object],
) -> tuple[list[tuple[str, IngestPipelineRequest]], list[str]]:
    """Prevent a request-level call/step quota from multiplying per document."""
    limits = [
        int(budgets[name])
        for name in ("max_llm_calls", "max_steps")
        if name in budgets and int(budgets[name]) > 0
    ]
    if not limits:
        return source_requests, []
    allowed = min(len(source_requests), min(limits))
    return source_requests[:allowed], [source_id for source_id, _ in source_requests[allowed:]]


def _partition_budgets(
    budgets: Mapping[str, object], count: int, index: int
) -> dict[str, object]:
    """Partition additive request budgets deterministically across source jobs."""
    if count <= 0:
        return dict(budgets)
    result: dict[str, object] = {}
    for name, value in budgets.items():
        if not isinstance(value, Real) or isinstance(value, bool):
            result[name] = value
            continue
        if name in {"max_llm_calls", "max_tokens", "max_steps"}:
            total = int(value)
            base, remainder = divmod(total, count)
            result[name] = base + (1 if index < remainder else 0)
        else:
            result[name] = float(value) / count
    return result


def _bounded_lens_arguments(arguments: Mapping[str, Any]) -> dict[str, object]:
    """Apply server-side result bounds instead of trusting agent-supplied limits."""
    result = dict(arguments)
    limits = {
        "hop_limit": 8,
        "max_nodes": 500,
        "max_edges": 2000,
        "max_hyperedges": 250,
    }
    for name, upper_bound in limits.items():
        if name not in result or result[name] is None:
            continue
        value = result[name]
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a non-negative integer")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a non-negative integer") from exc
        result[name] = max(0, min(value, upper_bound))
    return result


def _validate_supplied_provenance(
    provenance: Mapping[str, Any],
    *,
    workspace_id: str,
    source_uri: str,
    raw_text: str,
    require_identity: bool = False,
) -> None:
    declared_workspace = provenance.get("workspace_id")
    if require_identity and not declared_workspace:
        raise ValueError("required provenance must include workspace_id")
    if declared_workspace is not None and str(declared_workspace) != workspace_id:
        raise ValueError("provenance workspace_id does not match request workspace")
    declared_uri = provenance.get("source_uri")
    if require_identity and not declared_uri:
        raise ValueError("required provenance must include source_uri")
    if declared_uri is not None and str(declared_uri) != source_uri:
        raise ValueError("provenance source_uri does not match request source_uri")
    start = provenance.get("start_char")
    end = provenance.get("end_char")
    excerpt = provenance.get("excerpt")
    if start is None and end is None and excerpt is None:
        return
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(raw_text):
        raise ValueError("provenance span must be a valid half-open [start_char, end_char) range")
    if excerpt is not None and str(excerpt) != raw_text[start:end]:
        raise ValueError("provenance excerpt does not match raw_text span")


def _validate_agent_source_uri(source_uri: str) -> None:
    """Reject local paths and unsafe URI forms at the agent boundary."""
    if not source_uri or "\\" in source_uri:
        raise ValueError("source_uri must be a non-local URI")
    if len(source_uri) >= 2 and source_uri[1] == ":" and source_uri[0].isalpha():
        raise ValueError("local filesystem source_uri values are not accepted")
    parsed = urlparse(source_uri)
    if not parsed.scheme or parsed.scheme.lower() in {"file", "data", "javascript"}:
        raise ValueError("local filesystem and executable source_uri schemes are not accepted")
    if parsed.scheme.lower() in {"http", "https"}:
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("source_uri must be a credential-free http(s) URL")


def _validate_reingest_revision(existing: Mapping[str, Any], provenance: object) -> None:
    if not isinstance(provenance, Mapping):
        return
    requested = str(provenance.get("source_revision_id") or "").strip()
    if not requested:
        return
    known: set[str] = set()
    for revision in existing.get("revisions") or []:
        if not isinstance(revision, Mapping):
            continue
        known.add(str(revision.get("id") or ""))
        metadata = revision.get("metadata")
        if isinstance(metadata, Mapping):
            known.add(str(metadata.get("source_revision_id") or ""))
    if requested not in known:
        raise ValueError("provenance source_revision_id does not resolve to the existing source")


def _fetch_source_text(source_uri: str) -> str:
    parsed = urlparse(source_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("source_uri fetching requires a credential-free http(s) URL")
    allowed = {item.strip().lower() for item in os.getenv("LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS", "").split(",") if item.strip()}
    if parsed.hostname.lower() not in allowed:
        raise ValueError("source_uri host is not allowlisted by LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS")
    max_bytes = int(os.getenv("LLM_WIKI_SOURCE_FETCH_MAX_BYTES", "5000000"))
    timeout = max(1.0, float(os.getenv("LLM_WIKI_SOURCE_FETCH_TIMEOUT_SECONDS", "30")))

    class NoRedirect(urllib_request.HTTPRedirectHandler):
        def redirect_request(self, *_args: object, **_kwargs: object):
            raise ValueError("source_uri redirects are not permitted")

    request = urllib_request.Request(source_uri, headers={"accept": "text/plain, text/markdown, text/html"})
    with urllib_request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError("source_uri response exceeds LLM_WIKI_SOURCE_FETCH_MAX_BYTES")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("source_uri response exceeds LLM_WIKI_SOURCE_FETCH_MAX_BYTES")
    return data.decode("utf-8")


def _redact_source_text(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_source_text(item)
            for key, item in value.items()
            if key != "source_raw_text"
        }
    if isinstance(value, list):
        return [_redact_source_text(item) for item in value]
    return value


def _decode_metadata_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return {str(key): item for key, item in decoded.items()}
    return None


def _node_json(
    node: object | None,
    *,
    redact_source_text: bool = False,
) -> dict[str, object] | None:
    if node is None:
        return None
    dump = getattr(node, "model_dump", None)
    if callable(dump):
        try:
            value = dump(dump_format="json")
            return _redact_source_text(value) if redact_source_text else value
        except TypeError:
            value = dump(mode="json")
            return _redact_source_text(value) if redact_source_text else value
    value = {"id": str(getattr(node, "id", "")), "metadata": dict(getattr(node, "metadata", {}) or {})}
    return _redact_source_text(value) if redact_source_text else value


def _count_job_statuses(jobs: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _content_to_text(value: object) -> str:
    """Extract text from OpenAI/A2A content parts without leaking Python reprs."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "text" in value:
            return _content_to_text(value["text"])
        if "content" in value:
            return _content_to_text(value["content"])
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(
            text for text in (_content_to_text(item) for item in value) if text
        )
    return "" if value is None else str(value)


def _answer_text(result: Mapping[str, Any]) -> str:
    answer = result.get("answer")
    return str(answer.get("text") if isinstance(answer, Mapping) else answer or "")


def _request_id(payload: Mapping[str, Any], prefix: str) -> str:
    return str(payload.get("id") or f"{prefix}_{uuid.uuid4().hex}")


def _a2a_task(interaction: Mapping[str, Any], *, standard: bool = False) -> dict[str, object]:
    if isinstance(interaction.get("status"), Mapping):
        result = dict(interaction)
        result.setdefault("contextId", (result.get("metadata") or {}).get("workspace_id", "default"))
        return result
    status = str(interaction.get("status") or "pending")
    state = {"pending": "submitted" if standard else "working", "completed": "completed", "failed": "failed"}.get(status, status)
    response = interaction.get("response")
    text = _answer_text(response) if isinstance(response, Mapping) else ""
    result: dict[str, object] = {
        "id": interaction.get("interaction_id"),
        "contextId": interaction.get("context_id") or interaction.get("workspace_id") or "default",
        "status": {"state": state},
        "metadata": {"workspace_id": interaction.get("workspace_id")},
    }
    if text:
        result["artifacts"] = [{"parts": [{"kind": "text", "text": text}]}]
    if interaction.get("error"):
        result["status"] = {"state": "failed", "message": {"messageId": interaction.get("interaction_id"), "parts": [{"kind": "text", "text": str(interaction["error"])}]}}
    return result


def _jsonrpc_result(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: object, code: int, message: str, data: object | None = None) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


__all__ = ["AgentGateway", "AgentTurn"]
