"""Execution path for bounded maintenance observation jobs."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Literal, cast

from kogwistar.id_provider import stable_id
from kogwistar.server.auth_middleware import can_access_security_scope

from ..configuration.workspace import WorkspaceNamespaces
from ..parsing.parse_views import ParseTarget, ParseViewResolver, ParseViewStore
from ..utils import _background_namespace, _temporary_namespace
from .maintenance_context import append_maintenance_round
from .maintenance_observation import (
    ObservationFinding,
    ObservationRuntimeLimits,
    ObservationSubject,
    SubjectKind,
    assess_observation_frame,
    build_observation_frame,
)
from .maintenance_strategies import (
    MaintenanceJobExecutionContext,
    MaintenanceWorkerLike,
)
from .observation_critic import (
    is_context_window_error,
    safe_observation_critic_error_code,
)
from .observation_evidence import (
    build_authoritative_source_evidence,
    compact_entity_record,
)


class MaintenanceObservationWorkerMixin(MaintenanceWorkerLike):
    """Run read-only observation and persist its bounded assessment."""

    def _handle_review_maintenance_subject(self, ctx: MaintenanceJobExecutionContext) -> None:
        raw_subject = ctx.payload.get("observation_subject")
        subject_data = dict(raw_subject) if isinstance(raw_subject, Mapping) else {}
        subject_id = str(subject_data.get("subject_id") or ctx.payload.get("subject_id") or "").strip()
        parse_member_id = str(
            subject_data.get("parse_member_id")
            or ctx.payload.get("parse_generation_member_id")
            or ""
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
        expected_namespace = WorkspaceNamespaces(ctx.workspace_id).curated_kg_space
        if namespace != expected_namespace:
            raise ValueError("review_maintenance_subject namespace is outside the workspace")
        if not subject_id and parse_member_id:
            with self._observation_namespace(expected_namespace):
                matching_nodes = list(
                    self.engines.kg.read.get_nodes(
                        where={"parse_generation_member_id": parse_member_id},
                        limit=1,
                    )
                )
            if matching_nodes:
                subject_id = str(getattr(matching_nodes[0], "id", "") or "").strip()
        if not subject_id:
            self._emit_trace(
                "maintenance_observation_skipped",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                reason="no_reviewable_parse_member",
            )
            if ctx.job_id and not self._advance_maintenance_plan(ctx):
                self._acknowledge_job(ctx)
            return
        subject = ObservationSubject(
            kind=cast(SubjectKind, subject_kind),
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
            source_digest=str(
                subject_data.get("source_digest") or ctx.payload.get("source_digest") or ""
            ).strip()
            or None,
            parse_member_id=parse_member_id or None,
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
        source_context, relation_context, neighborhood_context, parent_context = (
            self._authoritative_observation_context(ctx, subject, limits)
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
            relation_context=relation_context,
            neighborhood_context=neighborhood_context,
            parent_context=parent_context,
            token_budget=limits.token_budget,
            active_view_id=active_view_id,
            active_view_version=active_view_version,
            source_evidence_required=True,
        )
        source_evidence_verified = any(
            item.get("evidence_role") == "authoritative_source"
            and item.get("source_evidence_status") == "span_verified"
            for item in frame.source_context
        )
        if source_evidence_verified and frame.omitted_counts.get("source", 0) == 0:
            critic_status, critic_findings = self._run_observation_critic(ctx, frame)
        else:
            critic_status, critic_findings = "not_used", ()
            self._emit_trace(
                "maintenance_observation_critic_skipped_no_verified_source",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                source_evidence_status=next(
                    (
                        str(item.get("source_evidence_status"))
                        for item in frame.source_context
                        if item.get("evidence_role") == "authoritative_source"
                    ),
                    "source_evidence_omitted",
                ),
            )
        assessment = assess_observation_frame(
            frame,
            critic_status=critic_status,
            critic_findings=critic_findings,
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
        if frame.parent_context:
            parent_id = str(frame.parent_context[0].get("id") or "").strip()
            if parent_id:
                ctx.payload.setdefault("parent_subject_id", parent_id)
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
                "observation_frame": _redacted_frame_payload(frame),
                "assessment": assessment.model_dump(mode="json"),
            },
        )
        if ctx.job_id and assessment.critic_status == "blocked_context":
            self._emit_trace(
                "maintenance_job_aborted_context_limit",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
            )
            self._acknowledge_job(ctx)
        elif ctx.job_id and not continuation_scheduled and not self._advance_maintenance_plan(ctx):
            self._acknowledge_job(ctx)

    def _run_observation_critic(
        self,
        ctx: MaintenanceJobExecutionContext,
        frame: object,
    ) -> tuple[Literal["not_used", "succeeded", "failed", "blocked_context"], tuple[ObservationFinding, ...]]:
        """Invoke one bounded provider hook and fail closed on bad output."""

        critic = getattr(self, "observation_critic", None)
        if not callable(critic):
            return "not_used", ()
        budgets = ctx.payload.get("budgets")
        budget_state = ctx.payload.get("maintenance_budget_state")
        max_calls = int(budgets.get("max_llm_calls") or 0) if isinstance(budgets, Mapping) else 0
        used_calls = (
            int(budget_state.get("call_used") or 0)
            if isinstance(budget_state, Mapping)
            else 0
        )
        if max_calls > 0 and used_calls >= max_calls:
            self._emit_trace(
                "maintenance_observation_critic_budget_exhausted",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                call_budget=max_calls,
                call_used=used_calls,
            )
            return "failed", ()
        next_budget_state = dict(budget_state) if isinstance(budget_state, Mapping) else {}
        next_budget_state["call_used"] = used_calls + 1
        ctx.payload["maintenance_budget_state"] = next_budget_state
        try:
            result = critic(frame, ctx)
            if not isinstance(result, Mapping):
                raise TypeError("observation critic must return an object")
            status = str(result.get("status") or "failed").strip().lower()
            if status not in {"succeeded", "failed"}:
                raise ValueError("observation critic status is invalid")
            raw_findings = result.get("findings") or ()
            if not isinstance(raw_findings, (list, tuple)):
                raise TypeError("observation critic findings must be a list")
            findings = tuple(
                ObservationFinding.model_validate(item)
                for item in raw_findings
            )
            return cast(Literal["succeeded", "failed"], status), findings
        except Exception as exc:  # noqa: BLE001 - critic failure is fail-closed
            if is_context_window_error(exc):
                self._emit_trace(
                    "maintenance_observation_context_limit_blocked",
                    workspace_id=ctx.workspace_id,
                    job_id=ctx.job_id,
                    error_type=type(exc).__name__,
                )
                pause_background = getattr(self, "context_limit_sink", None)
                if callable(pause_background):
                    try:
                        pause_background()
                    except Exception as pause_error:  # noqa: BLE001 - keep assessment fail-closed
                        self._emit_trace(
                            "maintenance_observation_context_pause_failed",
                            workspace_id=ctx.workspace_id,
                            job_id=ctx.job_id,
                            error_type=type(pause_error).__name__,
                        )
                return "blocked_context", ()
            self._emit_trace(
                "maintenance_observation_critic_failed",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
                error_code=safe_observation_critic_error_code(exc),
            )
            return "failed", ()

    def _schedule_observation_continuation(
        self,
        ctx: MaintenanceJobExecutionContext,
        action: str,
        continuation_allowed: bool,
    ) -> bool:
        """Requeue one bounded repair phase on the same maintenance job only."""

        if not continuation_allowed:
            return False
        current_round = _as_int(ctx.payload.get("maintenance_round"))
        max_rounds = _as_int(ctx.payload.get("maintenance_max_rounds"))
        if max_rounds > 0 and current_round + 1 >= max_rounds:
            return False
        last_scheduled_round = ctx.payload.get("observation_continuation_round")
        if last_scheduled_round is not None and _as_int(last_scheduled_round) == current_round:
            return False
        budgets = ctx.payload.get("budgets")
        budget_state = ctx.payload.get("maintenance_budget_state")
        max_steps = _as_int(budgets.get("max_steps")) if isinstance(budgets, Mapping) else 0
        used_steps = (
            _as_int(budget_state.get("step_used"))
            if isinstance(budget_state, Mapping)
            else 0
        )
        # The observation itself consumes one step. A continuation is allowed
        # only when one additional step remains after this assessment.
        if max_steps > 0 and used_steps + 1 >= max_steps:
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
        derived_parse_target: ParseTarget | None = None
        if next_kind == "document_reparse_region":
            raw_target = ctx.payload.get("parse_target")
            if isinstance(raw_target, Mapping):
                derived_parse_target = ParseTarget.model_validate(raw_target)
            else:
                derived_parse_target = self._derive_observation_parse_target(ctx)
            if derived_parse_target is None:
                # Never invent an unpinned region. The active ParseView must
                # provide an exact revision-grounded member first.
                return False
        if next_kind == "review_maintenance_subject" and not (
            ctx.payload.get("parent_subject_id") or ctx.payload.get("parent_member_id")
        ):
            return False
        if next_kind == "document_expand_parse_children" and not str(
            ctx.payload.get("parse_session_id") or ""
        ).strip():
            self._emit_trace(
                "maintenance_observation_continuation_blocked",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                action=action,
                reason="durable_parse_session_missing",
            )
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
            parent_subject_id = str(ctx.payload.get("parent_subject_id") or "").strip()
            if parent_subject_id:
                next_payload["subject_id"] = parent_subject_id
            else:
                next_payload.pop("subject_id", None)
                next_payload["parse_generation_member_id"] = str(
                    ctx.payload.get("parent_member_id") or ""
                )
        next_payload.update(
            {
                "maintenance_kind": next_kind,
                "maintenance_origin": "observation_continuation",
                "maintenance_previous_kind": ctx.maintenance_kind,
                "observation_action": action,
                "observation_continuation_id": continuation_id,
                "observation_continuation_scheduled": True,
                "observation_continuation_round": current_round,
                "maintenance_budget_state": {
                    **(dict(budget_state) if isinstance(budget_state, Mapping) else {}),
                    "step_used": used_steps + 1,
                },
                "maintenance_round": current_round + 1,
                "maintenance_plan": [next_kind],
                "maintenance_phase_index": 0,
            }
        )
        if action == "switch_to_excerpt":
            next_payload["split_strategy"] = "excerpt_first"
        elif action == "switch_to_boundary":
            next_payload["split_strategy"] = "boundary_first"
        if derived_parse_target is not None:
            next_payload["parse_target"] = derived_parse_target.model_dump(mode="json")
        raw_context = ctx.payload.get("maintenance_context")
        next_payload["maintenance_context"] = append_maintenance_round(
            cast(Mapping[str, object], raw_context)
            if isinstance(raw_context, Mapping)
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

    def _derive_observation_parse_target(
        self, ctx: MaintenanceJobExecutionContext
    ) -> ParseTarget | None:
        """Build a target only from the active, revision-pinned ParseView."""

        source_document_id = str(ctx.payload.get("source_document_id") or "").strip()
        source_revision_id = str(ctx.payload.get("source_revision_id") or "").strip()
        revision_document_id = str(ctx.payload.get("revision_document_id") or "").strip()
        member_id = str(ctx.payload.get("parse_generation_member_id") or "").strip()
        if not all((source_document_id, source_revision_id, revision_document_id, member_id)):
            return None
        metadata_store = getattr(getattr(self.engines, "conversation", None), "meta_sqlite", None)
        if metadata_store is None:
            return None
        view = ParseViewStore(
            metadata_store, workspace_id=ctx.workspace_id
        ).get(source_document_id)
        if view is None:
            return None
        if (
            view.workspace_id != ctx.workspace_id
            or view.source_document_id != source_document_id
            or view.source_revision_id != source_revision_id
            or view.revision_document_id != revision_document_id
        ):
            return None
        selection = next(
            (item for item in view.selections if item.member_id == member_id),
            None,
        )
        if selection is None:
            return None
        return ParseTarget(
            source_document_id=source_document_id,
            source_revision_id=source_revision_id,
            revision_document_id=revision_document_id,
            region=selection.region,
            reason="maintenance observation requested bounded quality reparse",
            parser_profile=str(ctx.payload.get("parser_profile") or "maintenance-reparse"),
            generation_member_id=member_id,
            llm_provider=(str(ctx.payload["llm_provider"]) if ctx.payload.get("llm_provider") else None),
            llm_model=(str(ctx.payload["llm_model"]) if ctx.payload.get("llm_model") else None),
        )

    def _authoritative_observation_context(
        self,
        ctx: MaintenanceJobExecutionContext,
        subject: ObservationSubject,
        limits: ObservationRuntimeLimits,
    ) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]], list[Mapping[str, object]], list[Mapping[str, object]]]:
        """Read the review frame from scoped graph state, never job payloads."""

        namespaces = WorkspaceNamespaces(ctx.workspace_id)
        namespace = namespaces.curated_kg_space
        engine = self.engines.kg
        with _temporary_namespace(engine, namespace):
            if subject.kind == "node":
                subject_entities = list(engine.read.get_nodes(ids=[subject.subject_id], limit=1))
                all_edges = list(engine.read.get_edges(limit=512))
            else:
                subject_entities = list(engine.read.get_edges(ids=[subject.subject_id], limit=1))
                all_edges = list(engine.read.get_edges(limit=512))
            if not subject_entities:
                raise ValueError("observation subject is not an active entity in the workspace graph")
            subject_entity = subject_entities[0]
            _validate_entity_scope(subject_entity, workspace_id=ctx.workspace_id)
            subject_record = compact_entity_record(
                subject_entity, workspace_id=ctx.workspace_id, namespace=namespace
            )

            related_edges = [
                edge for edge in all_edges
                if subject.subject_id in {
                    str(value) for value in getattr(edge, "source_ids", ()) or ()
                } | {
                    str(value) for value in getattr(edge, "target_ids", ()) or ()
                }
            ]
            for edge in related_edges:
                _validate_entity_scope(edge, workspace_id=ctx.workspace_id)
            neighbor_ids = sorted(
                {
                    str(value)
                    for edge in related_edges
                    for value in (
                        *(getattr(edge, "source_ids", ()) or ()),
                        *(getattr(edge, "target_ids", ()) or ()),
                    )
                    if str(value) != subject.subject_id
                }
            )[: limits.neighborhood_count]
            neighbors = list(engine.read.get_nodes(ids=neighbor_ids, limit=len(neighbor_ids))) if neighbor_ids else []

        source_evidence = build_authoritative_source_evidence(
            engine,
            subject_entity,
            workspace_id=ctx.workspace_id,
            source_namespace=namespaces.source_space,
            expected_source_document_id=subject.source_document_id,
            expected_revision_id=subject.revision_id,
            expected_revision_document_id=subject.revision_document_id,
            expected_source_digest=subject.source_digest,
        )
        source_context: list[Mapping[str, object]] = [source_evidence, subject_record]
        relation_context: list[Mapping[str, object]] = [
            compact_entity_record(edge, workspace_id=ctx.workspace_id, namespace=namespace)
            for edge in related_edges[:64]
        ]
        neighborhood_context: list[Mapping[str, object]] = [
            compact_entity_record(node, workspace_id=ctx.workspace_id, namespace=namespace)
            for node in neighbors
            if _validate_entity_scope(node, workspace_id=ctx.workspace_id)
        ]
        parent_context: list[Mapping[str, object]] = []
        subject_metadata = subject_record.get("metadata")
        parent_id = str(
            (subject_metadata.get("parent_member_id") if isinstance(subject_metadata, Mapping) else "")
            or ""
        ).strip()
        if limits.ancestor_hops and parent_id:
            with _temporary_namespace(engine, namespace):
                parent_nodes = list(engine.read.get_nodes(ids=[parent_id], limit=1))
            if parent_nodes:
                _validate_entity_scope(parent_nodes[0], workspace_id=ctx.workspace_id)
                parent_context = [
                    compact_entity_record(
                        parent_nodes[0], workspace_id=ctx.workspace_id, namespace=namespace
                    )
                ]
        return source_context, relation_context, neighborhood_context, parent_context

    def _persist_observation_audit(self, ctx, frame, assessment) -> None:
        """Persist assessment metadata without storing raw source text."""

        ns = WorkspaceNamespaces(ctx.workspace_id)
        audit_key = f"observation:{assessment.assessment_id}"
        try:
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
                        "frame": _redacted_frame_payload(frame),
                        "assessment": assessment.model_dump(mode="json"),
                    },
                    idempotency_key=audit_key,
                )
        except NotImplementedError:
            # Some metadata stores can idempotently project messages but do not
            # implement the optional row lookup used when replaying a message.
            # Accept only a matching graph record in this workspace namespace.
            with self._observation_namespace(ns.conv_bg):
                persisted = self.engines.conversation.read.get_nodes(
                    where={
                        "artifact_kind": "lane_message",
                        "idempotency_key": audit_key,
                        "msg_type": "maintenance.observation.assessment",
                    },
                    limit=1,
                )
            if not persisted:
                raise

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

    def _observation_namespace(self, namespace: str) -> AbstractContextManager[None]:
        return _background_namespace(self.engines.conversation, namespace)


def _validate_entity_scope(entity: object, *, workspace_id: str) -> bool:
    """Fail closed when a graph entity lacks the worker's ACL boundary."""

    raw_metadata = getattr(entity, "metadata", None)
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    if str(metadata.get("workspace_id") or "") != workspace_id:
        raise ValueError("observation entity is outside the claimed workspace")
    acl_scope = str(
        metadata.get("acl_scope")
        or metadata.get("security_scope")
        or ""
    ).strip()
    if acl_scope and not can_access_security_scope(acl_scope):
        raise PermissionError("observation entity is outside the current security scope")
    return True


def _redacted_frame_payload(frame: object) -> dict[str, object]:
    """Persist source identifiers and hashes, never transient source excerpts."""

    model_dump = getattr(frame, "model_dump", None)
    raw_payload = model_dump(mode="json") if callable(model_dump) else {}
    payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    source_context = payload.get("source_context")
    if isinstance(source_context, list):
        for record in source_context:
            if isinstance(record, dict):
                record.pop("source_excerpt", None)
    return payload


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        return int(value)
    return default


__all__ = ["MaintenanceObservationWorkerMixin"]
