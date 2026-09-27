"""Execution path for bounded maintenance observation jobs."""

from __future__ import annotations

from collections.abc import Mapping

from kogwistar.id_provider import stable_id

from ..configuration.workspace import WorkspaceNamespaces
from .maintenance_context import append_maintenance_round
from ..parsing.parse_views import ParseViewResolver
from .maintenance_observation import (
    ObservationRuntimeLimits,
    ObservationSubject,
    assess_observation_frame,
    build_observation_frame,
)
from .maintenance_strategies import MaintenanceJobExecutionContext


class MaintenanceObservationWorkerMixin:
    """Run read-only observation and persist its bounded assessment."""

    def _handle_review_maintenance_subject(self, ctx: MaintenanceJobExecutionContext) -> None:
        raw_subject = ctx.payload.get("observation_subject")
        subject_data = dict(raw_subject) if isinstance(raw_subject, Mapping) else {}
        subject_id = str(
            subject_data.get("subject_id")
            or ctx.payload.get("subject_id")
            or ctx.request_node_id
        ).strip()
        subject_kind = str(
            subject_data.get("kind") or ctx.payload.get("subject_kind") or "node"
        ).strip().lower()
        if subject_kind not in {"node", "edge", "hyperedge"}:
            raise ValueError("review_maintenance_subject requires node, edge, or hyperedge")
        namespace = str(
            subject_data.get("namespace")
            or ctx.payload.get("namespace")
            or WorkspaceNamespaces(ctx.workspace_id).curated_kg_space
        ).strip()
        if not namespace.startswith(f"ws:{ctx.workspace_id}:"):
            raise ValueError("review_maintenance_subject namespace is outside the workspace")
        subject = ObservationSubject(
            kind=subject_kind,
            subject_id=subject_id,
            workspace_id=ctx.workspace_id,
            namespace=namespace,
            source_document_id=str(
                subject_data.get("source_document_id")
                or ctx.payload.get("source_document_id")
                or ""
            ).strip()
            or None,
            revision_id=str(subject_data.get("revision_id") or ctx.payload.get("source_revision_id") or "") or None,
            revision_document_id=str(
                subject_data.get("revision_document_id")
                or ctx.payload.get("revision_document_id")
                or ""
            ).strip()
            or None,
            parse_member_id=str(
                subject_data.get("parse_member_id")
                or ctx.payload.get("parse_generation_member_id")
                or ""
            )
            or None,
            acl_scope=str(subject_data.get("acl_scope") or ctx.payload.get("acl_scope") or "") or None,
            embedding_profile_fingerprint=str(
                subject_data.get("embedding_profile_fingerprint")
                or ctx.payload.get("embedding_profile_fingerprint")
                or ""
            ).strip()
            or None,
        )
        limits = ObservationRuntimeLimits.from_payload(ctx.payload)
        if not limits.is_due(str(ctx.job_id or ctx.request_node_id)):
            self._emit_trace(
                "maintenance_observation_sampled_out",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                probability=limits.observation_probability,
            )
            self._emit_lane_reply(
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                reply_to_message_id=ctx.lane_message_id or None,
                status="completed",
                payload={
                    "maintenance_kind": ctx.maintenance_kind,
                    "observation_sampled_out": True,
                    "graph_mutation": False,
                },
            )
            if ctx.job_id:
                self._acknowledge_job(ctx)
            return
        active_view_id, active_view_version, active = self._resolve_active_parse_view(subject)
        source_context = _records(
            ctx.payload.get("observation_source_context"),
            workspace_id=ctx.workspace_id,
            namespace=subject.namespace,
            limit=64,
        )
        if not active:
            source_context.append(
                {
                    "evidence_id": subject.parse_member_id or subject.subject_id,
                    "quality_status": "review_required",
                    "parse_view_status": "inactive_or_unresolved",
                }
            )
        frame = build_observation_frame(
            subject,
            source_context=source_context,
            relation_context=_records(
                ctx.payload.get("observation_relation_context"),
                workspace_id=ctx.workspace_id,
                namespace=subject.namespace,
                limit=64,
            ),
            neighborhood_context=_records(
                ctx.payload.get("observation_neighborhood_context"),
                workspace_id=ctx.workspace_id,
                namespace=subject.namespace,
                limit=limits.neighborhood_count,
            ),
            parent_context=_records(
                ctx.payload.get("observation_parent_context"),
                workspace_id=ctx.workspace_id,
                namespace=subject.namespace,
                limit=1 if limits.ancestor_hops else 0,
            )[:1],
            token_budget=limits.token_budget,
            active_view_id=active_view_id,
            active_view_version=active_view_version,
        )
        assessment = assess_observation_frame(
            frame,
            critic_failed=bool(ctx.payload.get("observation_critic_failed")),
            watermark_expiry_seconds=limits.watermark_expiry_seconds,
        )
        self._emit_trace(
            "maintenance_observation_completed",
            workspace_id=ctx.workspace_id,
            job_id=ctx.job_id,
            request_node_id=ctx.request_node_id,
            assessment=assessment.model_dump(mode="json"),
            omitted_counts=frame.omitted_counts,
        )
        self._persist_observation_audit(ctx, frame, assessment)
        continuation_scheduled = self._schedule_observation_continuation(
            ctx, assessment.recommended_action, assessment.continuation_allowed
        )
        self._emit_lane_reply(
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            reply_to_message_id=ctx.lane_message_id or None,
            status="completed",
            payload={
                "maintenance_kind": ctx.maintenance_kind,
                "observation_frame": frame.model_dump(mode="json"),
                "assessment": assessment.model_dump(mode="json"),
            },
        )
        if ctx.job_id and not continuation_scheduled:
            self._acknowledge_job(ctx)

    def _schedule_observation_continuation(
        self,
        ctx: MaintenanceJobExecutionContext,
        action: str,
        continuation_allowed: bool,
    ) -> bool:
        """Requeue one bounded repair phase on the same maintenance job only."""

        if not continuation_allowed or ctx.payload.get("observation_continuation_scheduled"):
            return False
        current_round = int(ctx.payload.get("maintenance_round") or 0)
        max_rounds = int(ctx.payload.get("maintenance_max_rounds") or 0)
        if max_rounds > 0 and current_round + 1 >= max_rounds:
            return False
        next_kind_by_action = {
            "review_parent": "review_maintenance_subject",
            "expand_children": "document_expand_parse_children",
            "retry_same_strategy": "document_reparse_region",
            "switch_to_excerpt": "document_reparse_region",
            "switch_to_boundary": "document_reparse_region",
            "reparse_region": "document_reparse_region",
            "propose_relation_patch": "document_propose_crosslinks",
            "validate_crosslinks": "document_validate_crosslinks",
        }
        next_kind = next_kind_by_action.get(action)
        if next_kind is None:
            return False
        if next_kind == "review_maintenance_subject" and not (
            ctx.payload.get("parent_subject_id") or ctx.payload.get("parent_member_id")
        ):
            return False
        continuation_id = str(
            stable_id(
                "kogwistar_llm_wiki.observation_continuation",
                ctx.workspace_id,
                ctx.request_node_id,
                action,
                str(current_round + 1),
            )
        )
        next_payload = dict(ctx.payload)
        if next_kind == "review_maintenance_subject":
            next_payload["subject_id"] = str(
                ctx.payload.get("parent_subject_id") or ctx.payload.get("parent_member_id")
            )
        next_payload.update(
            {
                "maintenance_kind": next_kind,
                "maintenance_origin": "observation_continuation",
                "maintenance_previous_kind": ctx.maintenance_kind,
                "observation_action": action,
                "observation_continuation_id": continuation_id,
                "observation_continuation_scheduled": True,
                "maintenance_round": current_round + 1,
                "maintenance_plan": [next_kind],
                "maintenance_phase_index": 0,
            }
        )
        if action == "switch_to_excerpt":
            next_payload["split_strategy"] = "excerpt_first"
        elif action == "switch_to_boundary":
            next_payload["split_strategy"] = "boundary_first"
        next_payload["maintenance_context"] = append_maintenance_round(
            ctx.payload.get("maintenance_context")
            if isinstance(ctx.payload.get("maintenance_context"), Mapping)
            else None,
            round_number=current_round + 1,
            summary=f"observation requested {action}",
            touched_node_ids=[str(ctx.payload.get("subject_id") or ctx.request_node_id)],
            next_seed_node_ids=[str(next_payload.get("subject_id") or "")],
        )
        self.engines.conversation.jobs.requeue_at_tail(ctx.job, payload=next_payload)
        self._emit_trace(
            "maintenance_observation_continuation_scheduled",
            workspace_id=ctx.workspace_id,
            job_id=ctx.job_id,
            continuation_id=continuation_id,
            next_kind=next_kind,
            action=action,
            round=current_round + 1,
        )
        return True

    def _persist_observation_audit(self, ctx, frame, assessment) -> None:
        """Persist assessment metadata without storing raw source text."""

        ns = WorkspaceNamespaces(ctx.workspace_id)
        audit_key = f"observation:{assessment.assessment_id}"
        with self._observation_namespace(ns.conv_bg):
            self.engines.conversation.send_lane_message(
                conversation_id=f"maintenance:{ctx.request_node_id}",
                inbox_id="inbox:worker:maintenance:observation",
                sender_id="lane:worker:maintenance",
                recipient_id="lane:worker:maintenance-audit",
                msg_type="maintenance.observation.assessment",
                purpose="internal",
                payload={
                    "workspace_id": ctx.workspace_id,
                    "job_id": ctx.job_id,
                    "frame": frame.model_dump(mode="json"),
                    "assessment": assessment.model_dump(mode="json"),
                },
                idempotency_key=audit_key,
            )

    def _resolve_active_parse_view(self, subject: ObservationSubject) -> tuple[str | None, int | None, bool]:
        """Resolve derivation activity before allowing a subject to be reviewed."""

        if not subject.source_document_id or not subject.parse_member_id:
            return None, None, True
        metadata_store = getattr(getattr(self.engines, "conversation", None), "meta_sqlite", None)
        if metadata_store is None:
            return None, None, False
        try:
            resolver = ParseViewResolver(metadata_store, workspace_id=subject.workspace_id)
            resolution = resolver.resolve(
                subject.source_document_id,
                fallback_revision_document_id=subject.revision_document_id,
            )
            active = not resolution.is_legacy and resolver.is_active_metadata(
                subject.source_document_id,
                {
                    "parse_generation_member_id": subject.parse_member_id,
                    "revision_document_id": subject.revision_document_id or "",
                },
                fallback_revision_document_id=subject.revision_document_id,
            )
            return resolution.view_id, resolution.view_version, active
        except Exception as exc:  # noqa: BLE001 - unresolved activity must fail closed
            self._emit_trace(
                "maintenance_observation_active_view_check_failed",
                workspace_id=subject.workspace_id,
                subject_id=subject.subject_id,
                error_type=type(exc).__name__,
            )
            return None, None, False

    def _observation_namespace(self, namespace: str):
        from ..utils import _temporary_namespace

        return _temporary_namespace(self.engines.conversation, namespace)


def _records(
    value: object,
    *,
    workspace_id: str,
    namespace: str,
    limit: int,
) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    records: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = dict(item)
        record_workspace = str(record.get("workspace_id") or "").strip()
        record_namespace = str(record.get("namespace") or "").strip()
        if record_workspace and record_workspace != workspace_id:
            continue
        if record_namespace and record_namespace != namespace:
            continue
        if record.get("acl_authorized") is False or record.get("authorized") is False:
            continue
        if record.get("profile_compatible") is False:
            continue
        records.append(record)
    return records[: max(0, int(limit))]


__all__ = ["MaintenanceObservationWorkerMixin"]
