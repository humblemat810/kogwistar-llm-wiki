"""Resolve small, revision-grounded source excerpts for maintenance review."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.server.auth_middleware import can_access_security_scope

from ..utils import _temporary_namespace

_MAX_EXCERPT_CHARS = 1_000
_SPAN_FIELDS = ("doc_id", "start_char", "end_char", "excerpt")


def _mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def entity_metadata(entity: object) -> dict[str, object]:
    """Unwrap backend metadata without exposing arbitrary model payloads."""

    metadata = _mapping(getattr(entity, "metadata", None))
    nested = _mapping(metadata.get("metadata"))
    if nested:
        metadata = {**metadata, **nested}
    properties = _mapping(metadata.get("properties"))
    properties.update(_mapping(getattr(entity, "properties", None)))
    return {**metadata, **properties}


def compact_entity_record(
    entity: object, *, workspace_id: str, namespace: str
) -> dict[str, object]:
    """Keep only bounded review fields instead of backend-sized metadata blobs."""

    metadata = entity_metadata(entity)
    scalar_fields = (
        "relation",
        "quality_status",
        "parse_status",
        "source_document_id",
        "source_revision_id",
        "revision_id",
        "revision_document_id",
        "source_uri",
        "source_digest",
        "source_namespace",
        "target_namespace",
        "target_kind",
        "target_id",
        "artifact_kind",
        "knowledge_layer",
        "extraction_status",
        "verification_status",
        "parent_member_id",
        "member_id",
        "parse_generation_member_id",
        "is_pointer",
    )
    compact_metadata: dict[str, object] = {}
    for key in scalar_fields:
        value = metadata.get(key)
        if isinstance(value, str):
            compact_metadata[key] = value[:300]
        elif isinstance(value, (bool, int, float)):
            compact_metadata[key] = value
    record: dict[str, object] = {
        "id": str(getattr(entity, "id", "") or "")[:256],
        "kind": type(entity).__name__.lower(),
        "workspace_id": workspace_id,
        "namespace": namespace,
        "authorized": True,
        "acl_authorized": True,
        "label": str(getattr(entity, "label", "") or metadata.get("label") or "")[:300],
        "summary": str(getattr(entity, "summary", "") or metadata.get("summary") or "")[:500],
        "metadata": compact_metadata,
    }
    for key in (
        "relation",
        "quality_status",
        "parse_status",
        "source_document_id",
        "source_revision_id",
        "revision_id",
        "revision_document_id",
        "source_uri",
        "source_digest",
        "evidence_ids",
        "provenance",
    ):
        value = metadata.get(key)
        if key == "evidence_ids" and isinstance(value, Sequence) and not isinstance(value, str):
            record[key] = [str(item)[:256] for item in value[:16]]
        elif key == "provenance":
            provenance = _mapping(value)
            record[key] = {
                name: str(provenance[name])[:300]
                for name in ("source_document_id", "source_revision_id", "source_digest")
                if isinstance(provenance.get(name), str)
            }
        elif isinstance(value, str):
            record[key] = value[:500]
        elif value is not None and key in {"quality_status", "parse_status"}:
            record[key] = str(value)[:128]
    properties = {
        key: compact_metadata[key]
        for key in ("target_namespace", "target_kind", "target_id", "source_namespace")
        if key in compact_metadata
    }
    if properties:
        record["properties"] = properties
    return record


def _entity_spans(entity: object) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    iter_evidence = getattr(entity, "iter_evidence", None)
    if callable(iter_evidence):
        try:
            values = cast(Iterable[object], iter_evidence())
            for span in values:
                model_dump = getattr(span, "model_dump", None)
                raw_data = model_dump(mode="python") if callable(model_dump) else None
                data = _mapping(raw_data) if raw_data is not None else _mapping(span)
                if data:
                    spans.append({key: data.get(key) for key in _SPAN_FIELDS})
        except Exception:  # noqa: BLE001 - malformed grounding must fail closed
            return []
    if spans:
        return spans
    mentions = getattr(entity, "mentions", ())
    if isinstance(mentions, str):
        try:
            mentions = json.loads(mentions)
        except (TypeError, ValueError):
            return []
    if not isinstance(mentions, Sequence) or isinstance(mentions, (str, bytes)):
        return []
    for mention in mentions:
        grounding = _mapping(mention)
        grounding_spans = grounding.get("spans", ())
        if not isinstance(grounding_spans, Sequence) or isinstance(grounding_spans, (str, bytes)):
            continue
        spans.extend(
            {key: _mapping(span).get(key) for key in _SPAN_FIELDS}
            for span in grounding_spans
            if _mapping(span)
        )
    return spans


def _unavailable_record(
    *, workspace_id: str, namespace: str, status: str, source_entity_id: str = ""
) -> dict[str, object]:
    return {
        "id": source_entity_id[:256] or f"source-evidence:{status}",
        "kind": "source_evidence",
        "evidence_role": "authoritative_source",
        "workspace_id": workspace_id,
        "namespace": namespace,
        "authorized": True,
        "acl_authorized": True,
        "source_evidence_status": status,
    }


def _verified_span_record(
    engine: GraphKnowledgeEngine,
    span: Mapping[str, object],
    *,
    workspace_id: str,
    source_namespace: str,
    source_entity: object,
    expected_source_document_id: str | None,
    expected_revision_id: str | None,
    expected_revision_document_id: str | None,
    expected_source_digest: str | None,
) -> dict[str, object]:
    document_id = str(span.get("doc_id") or "").strip()
    try:
        with _temporary_namespace(engine, source_namespace):
            document = engine.read.get_document(document_id)
    except Exception:  # noqa: BLE001 - any backend miss must fail closed
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_document_unavailable",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )

    document_metadata = entity_metadata(document)
    declared_workspace = str(document_metadata.get("workspace_id") or "").strip()
    if declared_workspace != workspace_id:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_workspace_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    acl_scope = str(
        document_metadata.get("acl_scope") or document_metadata.get("security_scope") or ""
    ).strip()
    if acl_scope and not can_access_security_scope(acl_scope):
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_acl_denied",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )

    content = getattr(document, "content", None)
    if not isinstance(content, str):
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_content_not_text",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    expected_digest = str(
        document_metadata.get("source_digest")
        or document_metadata.get("source_map_digest")
        or document_metadata.get("content_sha256")
        or ""
    ).strip().lower()
    actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_revision_id = str(document_metadata.get("source_revision_id") or "").strip()
    document_revision_document_id = str(
        document_metadata.get("revision_document_id") or getattr(document, "id", "") or ""
    ).strip()
    logical_source_document_id = str(
        document_metadata.get("logical_source_document_id")
        or document_metadata.get("source_document_id")
        or ""
    ).strip()
    if expected_source_document_id and logical_source_document_id != expected_source_document_id:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="logical_source_document_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    if expected_revision_id and document_revision_id != expected_revision_id:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_revision_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    if expected_revision_document_id and (
        document_revision_document_id != expected_revision_document_id
        or document_id != expected_revision_document_id
    ):
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_revision_document_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    if expected_source_digest and expected_source_digest.lower() != actual_digest:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="pinned_source_digest_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    if not expected_digest:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="legacy_evidence_unavailable",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    if expected_digest != actual_digest:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_digest_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )

    start, end = span.get("start_char"), span.get("end_char")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or end > len(content)
        or end - start > _MAX_EXCERPT_CHARS
    ):
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_span_invalid_or_unbounded",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    excerpt = content[start:end]
    stored_excerpt = str(span.get("excerpt") or "")
    if not excerpt.strip() or stored_excerpt and stored_excerpt != excerpt:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_span_excerpt_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )

    record = _unavailable_record(
        workspace_id=workspace_id,
        namespace=source_namespace,
        status="span_verified",
        source_entity_id=str(getattr(source_entity, "id", "") or ""),
    )
    record.update(
        {
            "source_document_id": document_id,
            "logical_source_document_id": logical_source_document_id,
            "source_digest": actual_digest,
            "source_revision_id": document_revision_id,
            "revision_document_id": document_revision_document_id,
            "span_start_char": start,
            "span_end_char": end,
            "source_excerpt": excerpt,
            "source_title": str(document_metadata.get("title") or "")[:200],
            "source_entity_label": str(
                getattr(source_entity, "label", "") or ""
            )[:300],
            "source_entity_summary": str(
                getattr(source_entity, "summary", "") or ""
            )[:400],
        }
    )
    return record


def build_authoritative_source_evidence(
    engine: GraphKnowledgeEngine,
    entity: object,
    *,
    workspace_id: str,
    source_namespace: str,
    expected_source_document_id: str | None = None,
    expected_revision_id: str | None = None,
    expected_revision_document_id: str | None = None,
    expected_source_digest: str | None = None,
) -> dict[str, object]:
    """Dereference only same-workspace source pointers and verify exact spans."""

    metadata = entity_metadata(entity)
    if str(metadata.get("workspace_id") or "").strip() != workspace_id:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="subject_workspace_mismatch",
            source_entity_id=str(getattr(entity, "id", "") or ""),
        )
    pinned_source_document_id = (
        expected_source_document_id
        or str(
            metadata.get("logical_source_document_id")
            or metadata.get("source_document_id")
            or ""
        ).strip()
        or None
    )

    target_namespace = str(metadata.get("target_namespace") or "").strip()
    target_id = str(metadata.get("target_id") or metadata.get("refers_to_id") or "").strip()
    target_kind = str(metadata.get("target_kind") or "node").strip().lower()
    source_entity = entity
    if target_namespace or target_id:
        if target_namespace != source_namespace or not target_id or target_kind not in {"node", "edge"}:
            return _unavailable_record(
                workspace_id=workspace_id,
                namespace=source_namespace,
                status="source_pointer_out_of_scope",
                source_entity_id=str(getattr(entity, "id", "") or ""),
            )
        try:
            with _temporary_namespace(engine, source_namespace):
                getter = engine.read.get_edges if target_kind == "edge" else engine.read.get_nodes
                targets = list(getter(ids=[target_id], limit=1))
        except Exception:  # noqa: BLE001 - any backend miss must fail closed
            targets = []
        if not targets:
            return _unavailable_record(
                workspace_id=workspace_id,
                namespace=source_namespace,
                status="source_pointer_target_unavailable",
                source_entity_id=target_id,
            )
        source_entity = targets[0]

    source_metadata = entity_metadata(source_entity)
    if str(source_metadata.get("workspace_id") or "").strip() != workspace_id:
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_entity_workspace_mismatch",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )
    acl_scope = str(
        source_metadata.get("acl_scope") or source_metadata.get("security_scope") or ""
    ).strip()
    if acl_scope and not can_access_security_scope(acl_scope):
        return _unavailable_record(
            workspace_id=workspace_id,
            namespace=source_namespace,
            status="source_entity_acl_denied",
            source_entity_id=str(getattr(source_entity, "id", "") or ""),
        )

    span_failure_status = "source_span_unavailable"
    for span in _entity_spans(source_entity):
        if not str(span.get("doc_id") or "").strip():
            continue
        record = _verified_span_record(
            engine,
            span,
            workspace_id=workspace_id,
            source_namespace=source_namespace,
            source_entity=source_entity,
            expected_source_document_id=pinned_source_document_id,
            expected_revision_id=expected_revision_id,
            expected_revision_document_id=expected_revision_document_id,
            expected_source_digest=expected_source_digest,
        )
        if record.get("source_evidence_status") == "span_verified":
            return record
        if span_failure_status == "source_span_unavailable":
            span_failure_status = str(
                record.get("source_evidence_status") or span_failure_status
            )
    return _unavailable_record(
        workspace_id=workspace_id,
        namespace=source_namespace,
        status=span_failure_status,
        source_entity_id=str(getattr(source_entity, "id", "") or ""),
    )


__all__ = ["build_authoritative_source_evidence", "compact_entity_record", "entity_metadata"]
