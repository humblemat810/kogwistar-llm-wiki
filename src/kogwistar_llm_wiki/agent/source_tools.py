"""Source and maintenance lookup helpers for the agent gateway."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from ..models import IngestPipelineRequest
from ..parsing.parse_views import ParseViewResolver
from ..utils import _temporary_namespace
from .gateway_source import (
    decode_metadata_mapping,
    fetch_source_text,
    validate_agent_source_uri,
    validate_supplied_provenance,
)


class AgentSourceMixin:
    """Keep source discovery and queue inspection out of the protocol façade."""

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
            raw_text = fetch_source_text(source_uri)
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("ingest requires non-empty raw_text or a fetchable source_uri")
        validate_agent_source_uri(source_uri)
        policy = str(arguments.get("provenance_policy") or "optional").strip().lower()
        if policy not in {"required", "optional", "disabled"}:
            raise ValueError("provenance_policy must be required, optional, or disabled")
        provenance = arguments.get("provenance")
        if provenance is not None and not isinstance(provenance, Mapping):
            raise ValueError("provenance must be an object when supplied")
        if policy == "required" and not isinstance(provenance, Mapping):
            raise ValueError("required provenance was not supplied")
        parse_limits = arguments.get("parse_limits") or {}
        if not isinstance(parse_limits, Mapping):
            raise TypeError("parse_limits must be an object when supplied")
        if isinstance(provenance, Mapping):
            validate_supplied_provenance(
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
            llm_provider=str(arguments.get("llm_provider"))
            if arguments.get("llm_provider") is not None
            else None,
            llm_model=str(arguments.get("llm_model"))
            if arguments.get("llm_model") is not None
            else None,
            provenance_policy=policy,
            provenance=dict(provenance) if isinstance(provenance, Mapping) else None,
            parse_limits=dict(parse_limits),
        )
        declared_source_id = (
            str((provenance or {}).get("source_document_id") or "").strip()
            if isinstance(provenance, Mapping)
            else ""
        )
        if declared_source_id:
            expected_source_id = self.api.pipeline._source_document_id(request)
            if declared_source_id != expected_source_id:
                raise ValueError("provenance source_document_id does not match request source identity")
        return request

    def _load_source_request(
        self,
        *,
        workspace_id: str,
        source_uri: str = "",
        source_document_id: str = "",
    ) -> IngestPipelineRequest | None:
        candidates = self._source_documents(workspace_id)
        by_id = next(
            (item for item in candidates if source_document_id and str(item["id"]) == source_document_id),
            None,
        )
        by_uri = next(
            (
                item
                for item in candidates
                if source_uri and str(item["metadata"].get("source_uri") or "") == source_uri
            ),
            None,
        )
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
            provenance=decode_metadata_mapping(metadata.get("provenance")),
            parse_limits=dict(metadata.get("parse_limits") or {}),
        )

    def _source_documents(self, workspace_id: str) -> list[dict[str, object]]:
        ns = self.api.pipeline.namespaces_for(workspace_id)
        with _temporary_namespace(self.api.pipeline.engines.kg, ns.source_space):
            nodes = self.api.pipeline.engines.kg.read.get_nodes(limit=None)
        resolver = ParseViewResolver(
            self.api.pipeline.engines.conversation.meta_sqlite,
            workspace_id=workspace_id,
        )
        candidates_by_source: dict[str, list[tuple[int, str, dict[str, object]]]] = {}
        for node in nodes:
            metadata = dict(getattr(node, "metadata", {}) or {})
            if metadata.get("graph_space") != "source" and metadata.get("artifact_kind") != "source_revision":
                continue
            declared_workspace = str(metadata.get("workspace_id") or "").strip()
            if declared_workspace and declared_workspace != workspace_id:
                continue
            source_id = str(
                metadata.get("logical_source_document_id")
                or metadata.get("source_document_id")
                or metadata.get("doc_id")
                or ""
            ).strip()
            raw_text = metadata.get("source_raw_text")
            if not isinstance(raw_text, str):
                raw_text = getattr(node, "content", None)
            if not source_id or not isinstance(raw_text, str):
                continue
            revision_document_id = str(metadata.get("revision_document_id") or "").strip()
            if not revision_document_id and metadata.get("legacy_alias"):
                revision_document_id = source_id
            try:
                created_at_ms = int(metadata.get("created_at_ms") or 0)
            except (TypeError, ValueError):
                created_at_ms = 0
            candidate = {
                "id": source_id,
                "metadata": metadata,
                "content": raw_text,
                "revision_document_id": revision_document_id,
            }
            candidates_by_source.setdefault(source_id, []).append(
                (created_at_ms, revision_document_id, candidate)
            )

        result: list[dict[str, object]] = []
        for source_id, candidates in candidates_by_source.items():
            fallback_revision = max(candidates, key=lambda item: item[0])[1]
            resolution = resolver.resolve(
                source_id,
                fallback_revision_document_id=fallback_revision,
            )
            active = [
                item
                for item in candidates
                if item[1] and item[1] == resolution.revision_document_id
            ]
            if not active and not resolution.is_legacy:
                continue
            selected = max(
                active or candidates,
                key=lambda item: (
                    1 if item[2]["metadata"].get("artifact_kind") == "source_revision" else 0,
                    item[0],
                ),
            )
            result.append(selected[2])
        return result

    def _source_ids_for_topic(self, workspace_id: str, topic: str) -> list[str]:
        terms = {term.lower() for term in topic.split() if len(term) > 2}
        matches = []
        for item in self._source_documents(workspace_id):
            haystack = " ".join(
                [str(item["content"]), json.dumps(item["metadata"], sort_keys=True)]
            ).lower()
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
                rows = self.api.pipeline.engines.conversation.jobs.list(
                    namespace=ns.maintenance_jobs,
                    status=status,
                    limit=10_000,
                )
            except Exception as exc:  # noqa: BLE001
                if errors is not None:
                    errors.append(f"{type(exc).__name__}: {exc}")
                rows = []
            for job in rows:
                if source_document_id and str(job.entity_id) != source_document_id:
                    continue
                public_status = "completed" if status == "DONE" else status.lower()
                jobs.append(
                    {
                        "job_id": str(job.job_id),
                        "status": public_status,
                        "entity_id": str(job.entity_id),
                        "job_kind": str(job.job_kind),
                        "payload": dict(job.payload),
                    }
                )
        return jobs
