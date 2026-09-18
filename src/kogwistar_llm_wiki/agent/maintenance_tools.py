"""Request-bounded maintenance MCP tool implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agent.protocol import (
    budgets as _budgets,
)
from ..agent.protocol import (
    limit_budgeted_sources as _limit_budgeted_sources,
)
from ..agent.protocol import (
    partition_budgets as _partition_budgets,
)
from ..maintenance.maintenance_control import configured_default_request_max_rounds
from ..models import IngestPipelineRequest


class AgentMaintenanceToolsMixin:
    def maintain(self, arguments: Mapping[str, Any]) -> dict[str, object]:
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        topic = str(arguments.get("topic") or "").strip()
        objective = str(arguments.get("objective") or arguments.get("policy") or "").strip()
        if not workspace_id or not topic and not arguments.get("source_document_ids"):
            raise ValueError("maintain requires workspace_id and topic or source_document_ids")
        budgets = _budgets(arguments)
        maintenance_context = arguments.get("maintenance_context")
        if maintenance_context is not None and not isinstance(maintenance_context, Mapping):
            raise TypeError("maintenance_context must be an object")
        parse_target = arguments.get("parse_target")
        if parse_target is not None and not isinstance(parse_target, Mapping):
            raise TypeError("parse_target must be an object")
        max_rounds = arguments.get("max_rounds")
        if max_rounds is not None and (
            isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 0
        ):
            raise TypeError("max_rounds must be a non-negative integer")
        if max_rounds is None:
            max_rounds = configured_default_request_max_rounds()
        raw_source_ids = arguments.get("source_document_ids") or ()
        if not isinstance(raw_source_ids, (list, tuple, set, frozenset)):
            raise TypeError("source_document_ids must be a list of IDs")
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
            raw_seed_ids = arguments.get("seed_node_ids") or []
            if not isinstance(raw_seed_ids, (list, tuple, set, frozenset)):
                raise TypeError("seed_node_ids must be a list of IDs")
            job_id = self.api.pipeline.create_maintenance_request(
                request=request,
                source_document_id=source_id,
                namespace=ns.conv_bg,
                maintenance_kind=str(arguments.get("maintenance_kind") or "document_propose_crosslinks"),
                topic=topic or None,
                objective=objective or None,
                budgets=_partition_budgets(budgets, len(source_requests), index),
                seed_node_ids=[str(value) for value in raw_seed_ids if str(value).strip()],
                maintenance_context=maintenance_context,
                max_rounds=max_rounds,
                parse_target=parse_target,
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


