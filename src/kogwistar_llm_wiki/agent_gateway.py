"""Backward-compatible imports for the grouped agent gateway implementation."""

from dataclasses import asdict
from urllib import request as urllib_request

from .agent.gateway import (
    AgentGateway,
    AgentTurn,
    _a2a_task,
    _answer_text,
    _bounded_lens_arguments,
    _count_job_statuses,
    _decode_metadata_mapping,
    _fetch_source_text,
    _jsonrpc_error,
    _jsonrpc_result,
    _node_json,
    _redact_source_text,
    _request_id,
    _request_payload,
    _temporary_namespace,
    _validate_agent_source_uri,
    _validate_reingest_revision,
    _validate_supplied_provenance,
)
from .models import IngestPipelineRequest
from .otel import LlmWikiTelemetry
from .parsing.parse_generation_store import ParseGenerationStore
from .parsing.parse_session_store import ParseSessionStore
from .parsing.parse_views import ParseViewResolver, parse_session_id
from .workbench.inspection import build_workspace_quality_report
from .workbench.workbench_api import WorkbenchApi

__all__ = [
    "AgentGateway",
    "AgentTurn",
    "IngestPipelineRequest",
    "LlmWikiTelemetry",
    "ParseGenerationStore",
    "ParseSessionStore",
    "ParseViewResolver",
    "WorkbenchApi",
    "_a2a_task",
    "_answer_text",
    "_bounded_lens_arguments",
    "_count_job_statuses",
    "_decode_metadata_mapping",
    "_fetch_source_text",
    "_jsonrpc_error",
    "_jsonrpc_result",
    "_node_json",
    "_redact_source_text",
    "_request_id",
    "_request_payload",
    "_temporary_namespace",
    "_validate_agent_source_uri",
    "_validate_reingest_revision",
    "_validate_supplied_provenance",
    "asdict",
    "build_workspace_quality_report",
    "parse_session_id",
    "urllib_request",
]
