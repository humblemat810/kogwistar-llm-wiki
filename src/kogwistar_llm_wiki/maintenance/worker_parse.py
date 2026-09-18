"""Durable layered parsing and selective reparse worker steps."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime

from kg_doc_parser.semantic_document_splitting_layerwise_edits import (
    parser_llm_cache_transaction,
)
from kogwistar.engine_core.models import GraphExtractionWithIDs
from kogwistar.id_provider import stable_id

from ..configuration.workspace import WorkspaceNamespaces
from ..ingest_pipeline import IngestPipeline, IngestPipelineRequest
from ..maintenance import MaintenanceJobExecutionContext
from ..parsing.parse_generation_store import (
    ParseGenerationStore,
    ParseGenerationStoreConflict,
)
from ..parsing.parse_reconciliation import decide_parse_reconciliation
from ..parsing.parse_session_store import ParseSessionStore, ParseSessionStoreConflict
from ..parsing.parse_views import (
    ParseFrontierItem,
    ParseGeneration,
    ParseGenerationCommit,
    ParseGenerationMember,
    ParseGenerationStatus,
    ParseSessionPhase,
    ParseSessionState,
    ParseView,
    ParseViewConflict,
    ParseViewSelection,
    ParseViewStore,
    SourceRegion,
    frontier_id,
    generation_member_id,
)
from ..utils import _temporary_namespace
from .state import semantic_fingerprint as _semantic_fingerprint


class DurableParseMaintenanceWorkerMixin:
    """Methods for bounded, revision-pinned parse expansion and reparsing."""

    def _handle_document_parse_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Run the application parser as one bounded maintenance phase."""
        decision = self._evaluate_maintenance_guard(ctx)
        if decision.status != "ready":
            self._block_guarded_job(ctx, decision)
            return
        if bool(ctx.payload.get("durable_layered_parse")):
            try:
                self._mark_durable_parse_expanding(ctx)
            except (ParseSessionStoreConflict, TypeError, ValueError, KeyError) as exc:
                self._emit_trace(
                    "maintenance_parse_failed",
                    workspace_id=ctx.workspace_id,
                    source_document_id=str(ctx.payload.get("source_document_id") or ""),
                    request_node_id=ctx.request_node_id,
                    job_id=ctx.job_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                self.engines.conversation.jobs.retry_or_fail(ctx.job, exc)
                return
            if self._advance_maintenance_plan(ctx):
                return
            self._acknowledge_job(ctx)
            return
        started_ms = int(time.time() * 1000)
        self._emit_trace(
            "maintenance_parse_start",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            maintenance_kind=ctx.maintenance_kind,
        )
        try:
            result: Mapping[str, object] = self.document_parser(ctx)
            if bool(result.get("stale_claim")) or getattr(self, "_claim_lost", threading.Event()).is_set():
                self._emit_trace(
                    "maintenance_repeated_work_discarded",
                    workspace_id=ctx.workspace_id,
                    source_document_id=str(ctx.payload.get("source_document_id") or ""),
                    request_node_id=ctx.request_node_id,
                    job_id=ctx.job_id,
                    reason="claim_lost_before_commit",
                    comparison_result={str(k): v for k, v in result.items()},
                )
                return
            self._emit_trace(
                "maintenance_parse_complete",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                job_id=ctx.job_id,
                node_count=int(result.get("node_count") or 0),
                edge_count=int(result.get("edge_count") or 0),
                llm_call_count=int(result.get("llm_call_count") or 0),
                duration_ms=int(time.time() * 1000) - started_ms,
            )
            if self._advance_maintenance_plan(ctx):
                return
            self._acknowledge_job(ctx)
        except Exception as exc:  # noqa: BLE001 - failed maintenance is reported and fenced
            self._emit_trace(
                "maintenance_parse_failed",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
                error=str(exc),
                duration_ms=int(time.time() * 1000) - started_ms,
            )
            self.engines.conversation.jobs.retry_or_fail(ctx.job, exc)

    def _mark_durable_parse_expanding(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Advance only durable session state; parsing occurs in the frontier job."""

        session_id = str(ctx.payload.get("parse_session_id") or "")
        if not session_id:
            raise ValueError("durable parse requires parse_session_id")
        store = ParseSessionStore(self.engines.conversation.meta_sqlite, workspace_id=ctx.workspace_id)
        stored = store.get(session_id)
        if stored is None:
            raise ValueError("durable parse session is missing")
        session, frontier, version = stored
        if not session.parser_state:
            raise ValueError("durable parse session has no recoverable parser state")
        if session.phase == ParseSessionPhase.STABLE:
            return
        store.save(
            session.model_copy(
                update={
                    "phase": ParseSessionPhase.EXPANDING,
                    "failure_reason": None,
                    "last_progress_at": datetime.now(UTC),
                }
            ),
            frontier,
            expected_version=version,
        )

    def _handle_document_expand_parse_children_strategy(
        self, ctx: MaintenanceJobExecutionContext
    ) -> None:
        """Run one durable frontier batch without spawning a new job."""

        decision = self._evaluate_maintenance_guard(ctx)
        if decision.status != "ready":
            self._block_guarded_job(ctx, decision)
            return
        session_id = str(ctx.payload.get("parse_session_id") or "")
        if not session_id:
            self._emit_trace(
                "maintenance_parse_expansion_blocked",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                job_id=ctx.job_id,
                reason="parse_session_id_missing",
            )
            self.engines.conversation.jobs.retry_or_fail(
                ctx.job,
                RuntimeError("parse_session_id is required for durable expansion"),
            )
            return
        store = ParseSessionStore(
            self.engines.conversation.meta_sqlite,
            workspace_id=ctx.workspace_id,
        )
        stored = store.get(session_id)
        if stored is None:
            self._emit_trace(
                "maintenance_parse_expansion_blocked",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                job_id=ctx.job_id,
                reason="parse_session_missing",
            )
            self.engines.conversation.jobs.retry_or_fail(
                ctx.job,
                RuntimeError("durable parse session is missing; refusing non-durable expansion"),
            )
            return
        try:
            session, frontier, version = self._recover_pending_parse_view(store, stored)
            result = self.layered_parser(ctx, session, frontier)
            next_session = ParseSessionState.model_validate(result.get("session", {}))
            self._validate_parse_session_transition(session, next_session)
            next_frontier = [
                ParseFrontierItem.model_validate(item)
                for item in (result.get("frontier") or [])
            ]
            consumed_values = [
                str(value)
                for value in (result.get("consumed_frontier_ids") or [])
                if str(value).strip()
            ]
            if len(consumed_values) != len(set(consumed_values)):
                raise ValueError("layered parser returned duplicate consumed frontier IDs")
            consumed_ids = set(consumed_values)
            if len(consumed_ids) > session.max_frontier_items:
                raise ValueError("layered parser consumed more than the configured frontier batch")
            if any(item.depth > session.max_depth for item in next_frontier):
                raise ValueError("layered parser returned a frontier beyond max_depth")
            if len(next_frontier) > len(frontier) + session.max_frontier_items:
                raise ValueError("layered parser returned an unbounded frontier")
            if bool(result.get("stable")) and next_frontier:
                raise ValueError("layered parser cannot report stable with pending frontier items")
            stable = bool(result.get("stable", not next_frontier))
            available_ids = {item.frontier_id for item in frontier}
            if frontier and not consumed_ids:
                raise ValueError("layered parser must report consumed_frontier_ids")
            if not consumed_ids.issubset(available_ids):
                raise ValueError("layered parser consumed frontier outside the claimed batch")
            next_frontier_ids = [item.frontier_id for item in next_frontier]
            if len(next_frontier_ids) != len(set(next_frontier_ids)):
                raise ValueError("layered parser returned duplicate frontier IDs")
            if not (available_ids - consumed_ids).issubset(next_frontier_ids):
                raise ValueError("layered parser dropped an unconsumed frontier item")
            next_session = next_session.model_copy(
                update={
                    "phase": ParseSessionPhase.STABLE if stable else ParseSessionPhase.EXPANDING,
                    "frontier_ids": tuple(item.frontier_id for item in next_frontier),
                    "consumed_frontier_ids": tuple(
                        sorted(set(session.consumed_frontier_ids).union(consumed_ids))
                    ),
                }
            )
            generation_payload = result.get("generation")
            commit_payload = result.get("commit")
            members_payload = result.get("members")
            if any(value is not None for value in (generation_payload, commit_payload, members_payload)):
                if not isinstance(generation_payload, Mapping) or not isinstance(commit_payload, Mapping):
                    raise ValueError("layered parser generation and commit payloads are required together")
                if not isinstance(members_payload, list):
                    raise ValueError("layered parser members must be a list")
                generation_store = ParseGenerationStore(
                    self.engines.conversation.meta_sqlite,
                    workspace_id=ctx.workspace_id,
                )
                generation = ParseGeneration.model_validate(generation_payload)
                if (
                    generation.workspace_id != ctx.workspace_id
                    or generation.generation_id != session.generation_id
                    or generation.source_document_id != session.source_document_id
                    or generation.source_revision_id != session.source_revision_id
                    or generation.source_digest != session.source_digest
                    or generation.revision_document_id != session.revision_document_id
                ):
                    raise ValueError("layered parser generation is not pinned to the session revision")
                generation_store.commit(
                    generation,
                    ParseGenerationCommit.model_validate(commit_payload),
                    [ParseGenerationMember.model_validate(item) for item in members_payload],
                )
            view_payload = result.get("parse_view")
            pending_version = version
            if view_payload is not None:
                if not isinstance(view_payload, Mapping):
                    raise ValueError("layered parser parse_view must be an object")
                view = ParseView.model_validate(view_payload)
                if (
                    view.workspace_id != ctx.workspace_id
                    or view.source_document_id != session.source_document_id
                    or view.source_revision_id != session.source_revision_id
                    or view.revision_document_id != session.revision_document_id
                ):
                    raise ValueError("layered parser ParseView is not pinned to the session source revision")
                view_store = ParseViewStore(
                    self.engines.conversation.meta_sqlite,
                    workspace_id=ctx.workspace_id,
                )
                current_view = view_store.get(view.source_document_id)
                expected_view_version = result.get("expected_view_version")
                if expected_view_version is None and current_view is not None:
                    raise ValueError("expected_view_version is required when replacing a ParseView")
                pending_session = next_session.model_copy(
                    update={"pending_view": view.model_dump(mode="json")}
                )
                pending_version = store.save(
                    pending_session,
                    next_frontier,
                    expected_version=version,
                )
                view_store.activate(
                    view,
                    expected_view_version=(
                        None
                        if expected_view_version is None
                        else int(expected_view_version)
                    ),
                )
                next_session = pending_session.model_copy(update={"pending_view": None})
                store.save(next_session, next_frontier, expected_version=pending_version)
            else:
                store.save(next_session, next_frontier, expected_version=version)
            if stable:
                reconciliation = result.get("reconciliation")
                review_required = isinstance(reconciliation, Mapping) and bool(
                    reconciliation.get("requires_review")
                )
                if not review_required:
                    self._record_durable_parse_readiness(ctx, next_session)
                if review_required:
                    self._emit_trace(
                        "maintenance_parse_reconciliation_review_required",
                        workspace_id=ctx.workspace_id,
                        source_document_id=str(ctx.payload.get("source_document_id") or ""),
                        job_id=ctx.job_id,
                        reconciliation=dict(reconciliation),
                    )
                    self._acknowledge_job(ctx)
                    return
                if self._advance_maintenance_plan(ctx):
                    return
                self._acknowledge_job(ctx)
            else:
                self.engines.conversation.jobs.requeue_at_tail(
                    ctx.job,
                    payload={**ctx.payload, "parse_session_id": next_session.session_id},
                )
            self._emit_trace(
                "maintenance_parse_expansion_complete",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                job_id=ctx.job_id,
                stable=stable,
                frontier_count=len(next_frontier),
            )
        except (
            ParseGenerationStoreConflict,
            ParseSessionStoreConflict,
            ParseViewConflict,
            TimeoutError,
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            self._emit_trace(
                "maintenance_parse_expansion_failed",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.engines.conversation.jobs.retry_or_fail(ctx.job, exc)

    def _record_durable_parse_readiness(
        self,
        ctx: MaintenanceJobExecutionContext,
        session: ParseSessionState,
    ) -> None:
        """Publish completion only after the durable frontier is actually stable."""

        ns = WorkspaceNamespaces(ctx.workspace_id)
        with _temporary_namespace(self.engines.kg, ns.source_space):
            document = self.engines.kg.read.get_document(session.revision_document_id)
        metadata = dict(document.metadata or {})
        request = IngestPipelineRequest(
            workspace_id=ctx.workspace_id,
            source_uri=str(metadata.get("source_uri") or session.revision_document_id),
            title=str(metadata.get("title") or session.revision_document_id),
            raw_text=str(document.content or ""),
            source_format=str(metadata.get("source_format") or "text"),
            operation_mode="maintenance_first",
            parser_mode=str(metadata.get("parser_mode") or "heuristic"),
            parser_lane=str(metadata.get("parser_lane") or "page_index"),
            promotion_mode=str(metadata.get("promotion_mode") or "pending"),
            llm_provider=(str(metadata["llm_provider"]) if metadata.get("llm_provider") else None),
            llm_model=(str(metadata["llm_model"]) if metadata.get("llm_model") else None),
            parse_limits={
                "max_depth": session.max_depth,
                "max_frontier_items": session.max_frontier_items,
                "max_parser_calls": session.max_parser_calls,
                "max_region_chars": session.max_region_chars,
                **({"token_budget": session.token_budget} if session.token_budget is not None else {}),
                **(
                    {"wall_time_seconds": session.wall_time_seconds}
                    if session.wall_time_seconds is not None
                    else {}
                ),
            },
        )
        IngestPipeline(self.engines).record_source_readiness(
            request=request,
            source_document_id=session.source_document_id,
            stage="parsed_graph_persisted",
        )

    @staticmethod
    def _validate_parse_session_transition(
        current: ParseSessionState,
        next_session: ParseSessionState,
    ) -> None:
        immutable_fields = (
            "session_id",
            "workspace_id",
            "source_document_id",
            "source_revision_id",
            "source_digest",
            "revision_document_id",
            "generation_id",
            "parser_state",
            "max_depth",
            "max_frontier_items",
            "max_parser_calls",
            "max_region_chars",
            "token_budget",
            "wall_time_seconds",
        )
        changed = [
            field
            for field in immutable_fields
            if getattr(current, field) != getattr(next_session, field)
        ]
        if changed:
            raise ValueError("layered parser changed immutable session fields: " + ", ".join(changed))
        if next_session.parser_calls < current.parser_calls:
            raise ValueError("layered parser decreased parser call count")
        if not set(current.consumed_frontier_ids).issubset(next_session.consumed_frontier_ids):
            raise ValueError("layered parser removed consumed frontier history")

    def _recover_pending_parse_view(
        self,
        store: ParseSessionStore,
        stored: tuple[ParseSessionState, list[ParseFrontierItem], int],
    ) -> tuple[ParseSessionState, list[ParseFrontierItem], int]:
        """Finish a view CAS left pending by a worker crash."""

        session, frontier, version = stored
        if not session.pending_view:
            return stored
        view = ParseView.model_validate(session.pending_view)
        view_store = ParseViewStore(
            self.engines.conversation.meta_sqlite,
            workspace_id=session.workspace_id,
        )
        current = view_store.get(view.source_document_id)
        if current is None:
            view_store.activate(view, expected_view_version=None)
        elif current.view_id == view.view_id and current.view_version == view.view_version:
            pass
        elif current.view_version > view.view_version:
            # A concurrent worker won with a newer complete view. The older
            # pending proposal is no longer actionable, but must be cleared so
            # the session does not retry forever.
            recovered = session.model_copy(update={"pending_view": None})
            recovered_version = store.save(recovered, frontier, expected_version=version)
            return recovered, frontier, recovered_version
        elif current.view_version == view.view_version:
            raise ParseViewConflict("pending ParseView conflicts at the active view version")
        else:
            view_store.activate(view, expected_view_version=current.view_version)
        recovered = session.model_copy(update={"pending_view": None})
        recovered_version = store.save(recovered, frontier, expected_version=version)
        return recovered, frontier, recovered_version

    def _expand_durable_parse_frontier(
        self,
        ctx: MaintenanceJobExecutionContext,
        session: ParseSessionState,
        frontier: list[ParseFrontierItem],
    ) -> Mapping[str, object]:
        """Parse one revision-pinned frontier and return an inactive derivation.

        The graph write is deliberately tagged with its generation member before
        the ParseView is activated by the caller. This keeps a crash between the
        write and the view CAS from exposing an unselected interpretation.
        """

        if not frontier:
            return {
                "session": session.model_dump(mode="json"),
                "frontier": [],
                "consumed_frontier_ids": [],
                "stable": True,
            }
        if session.parser_calls >= session.max_parser_calls:
            raise ValueError("durable parse parser-call budget is exhausted")
        selected = min(frontier, key=lambda item: (item.depth, item.ordinal))
        state = dict(session.parser_state)
        if int(state.get("schema_version") or 0) != 1:
            raise ValueError("durable parse session has no supported parser state")
        revision_document_id = str(state.get("source_revision_document_id") or "")
        if revision_document_id != session.revision_document_id:
            raise ValueError("durable parser state does not match the session revision")
        ns = WorkspaceNamespaces(ctx.workspace_id)
        with _temporary_namespace(self.engines.kg, ns.source_space):
            document = self.engines.kg.read.get_document(revision_document_id)
        raw_text = str(document.content or "")
        if not raw_text:
            raise ValueError("immutable source revision has no text to parse")
        if selected.region.end_char > len(raw_text):
            raise ValueError("parse frontier region exceeds immutable source bytes")
        view_store = ParseViewStore(
            self.engines.conversation.meta_sqlite,
            workspace_id=ctx.workspace_id,
        )
        current_view = view_store.get(session.source_document_id)
        if current_view is not None and current_view.revision_document_id != session.revision_document_id:
            raise ValueError("active ParseView targets a different immutable source revision")
        if ctx.maintenance_kind == "document_reparse_region":
            if current_view is None:
                raise ValueError(
                    "legacy_evidence_unavailable: targeted reparse requires an active ParseView"
                )
            self._validate_reparse_region_coverage(current_view, selected.region)

        token_region_chars = (
            session.token_budget * 4 if session.token_budget is not None else session.max_region_chars
        )
        effective_region_chars = min(session.max_region_chars, token_region_chars)
        if selected.region.end_char - selected.region.start_char > effective_region_chars:
            if selected.depth >= session.max_depth:
                raise ValueError(
                    "durable parse region exceeds max_region_chars at max_depth; "
                    "increase the explicit parse limit or select a smaller region"
                )
            split_at = min(
                selected.region.start_char + effective_region_chars,
                selected.region.end_char - 1,
            )
            # Prefer a whitespace boundary without creating an empty region.
            boundary = max(
                raw_text.rfind("\n", selected.region.start_char + 1, split_at + 1),
                raw_text.rfind(" ", selected.region.start_char + 1, split_at + 1),
            )
            split_at = boundary if boundary > selected.region.start_char else split_at
            regions = (
                SourceRegion(
                    source_document_id=selected.region.source_document_id,
                    start_char=selected.region.start_char,
                    end_char=split_at,
                ),
                SourceRegion(
                    source_document_id=selected.region.source_document_id,
                    start_char=split_at,
                    end_char=selected.region.end_char,
                ),
            )
            children = [
                ParseFrontierItem(
                    frontier_id=frontier_id(
                        session_id=session.session_id,
                        region=region,
                        ordinal=ordinal,
                    ),
                    session_id=session.session_id,
                    generation_id=session.generation_id,
                    workspace_id=session.workspace_id,
                    source_document_id=session.source_document_id,
                    source_revision_id=session.source_revision_id,
                    revision_document_id=session.revision_document_id,
                    parent_member_id=selected.parent_member_id,
                    region=region,
                    depth=selected.depth + 1,
                    ordinal=ordinal,
                )
                for ordinal, region in enumerate(regions)
            ]
            next_frontier = [item for item in frontier if item.frontier_id != selected.frontier_id]
            next_frontier.extend(children)
            return {
                "session": session.model_copy(
                    update={
                        "phase": ParseSessionPhase.EXPANDING,
                        "frontier_ids": tuple(item.frontier_id for item in next_frontier),
                        "consumed_frontier_ids": tuple(
                            sorted(set(session.consumed_frontier_ids) | {selected.frontier_id})
                        ),
                        "parser_calls": session.parser_calls + 1,
                        "last_progress_at": datetime.now(UTC),
                    }
                ).model_dump(mode="json"),
                "frontier": [item.model_dump(mode="json") for item in next_frontier],
                "consumed_frontier_ids": [selected.frontier_id],
                "stable": False,
                "diagnostics": {
                    "phase": "parse_expanding",
                    "reason": "region_segmented_before_parse",
                    "child_count": len(children),
                },
            }

        source_request = IngestPipelineRequest(
            workspace_id=ctx.workspace_id,
            source_uri=str(state.get("source_uri") or revision_document_id),
            title=str(state.get("title") or revision_document_id),
            raw_text=raw_text,
            source_format=str(state.get("source_format") or "text"),
            operation_mode="maintenance_first",
            parser_mode=str(state.get("parser_mode") or "heuristic"),
            parser_lane=str(state.get("parser_lane") or "page_index"),
            promotion_mode=str(state.get("promotion_mode") or "pending"),
            llm_provider=(str(state["llm_provider"]) if state.get("llm_provider") else None),
            llm_model=(str(state["llm_model"]) if state.get("llm_model") else None),
        )
        parser_request = source_request.model_copy(
            update={"raw_text": raw_text[selected.region.start_char : selected.region.end_char]}
        )
        pipeline = IngestPipeline(self.engines)
        with parser_llm_cache_transaction() as parser_cache_transaction:
            parse_started = time.monotonic()
            parse_result = pipeline.parse_source(
                request=parser_request,
                source_document_id=revision_document_id,
            )
            if (
                session.wall_time_seconds is not None
                and time.monotonic() - parse_started > session.wall_time_seconds
            ):
                raise TimeoutError("durable parse expansion exceeded wall-time budget")
            extraction = pipeline.translate_parse_result(
                parse_result=parse_result,
                source_document_id=revision_document_id,
            )
            self._offset_extraction_spans(extraction, selected.region.start_char)
            semantic_fingerprint = _semantic_fingerprint(extraction)
            commit_id = str(
                stable_id(
                    "kogwistar_llm_wiki.parse_generation_commit",
                    session.generation_id,
                    selected.frontier_id,
                )
            )
            member_id = generation_member_id(
                generation_id=session.generation_id,
                commit_id=commit_id,
                ordinal=selected.ordinal,
            )
            pipeline.ingest_parse_result(
                request=source_request,
                source_document_id=revision_document_id,
                graph_extraction=extraction,
                namespace=ns.conv_fg,
                parse_generation_id=session.generation_id,
                parse_generation_member_id=member_id,
                parse_region=selected.region,
            )
            parser_cache_transaction.promote()

        remaining_frontier = [
            item for item in frontier if item.frontier_id != selected.frontier_id
        ]
        stable = not remaining_frontier
        generation_store = ParseGenerationStore(
            self.engines.conversation.meta_sqlite,
            workspace_id=ctx.workspace_id,
        )
        stored_generation = generation_store.get(session.generation_id)
        if stored_generation is not None:
            generation = stored_generation[0]
            if (
                generation.source_document_id != session.source_document_id
                or generation.source_revision_id != session.source_revision_id
                or generation.source_digest != session.source_digest
                or generation.revision_document_id != session.revision_document_id
            ):
                raise ValueError("stored parse generation is not pinned to the session revision")
            generation = generation.model_copy(
                update={
                    "status": (
                        ParseGenerationStatus.STABLE
                        if stable
                        else ParseGenerationStatus.EXPANDING
                    )
                }
            )
        else:
            generation = ParseGeneration(
                generation_id=session.generation_id,
                workspace_id=session.workspace_id,
                source_document_id=session.source_document_id,
                source_revision_id=session.source_revision_id,
                source_digest=session.source_digest,
                revision_document_id=session.revision_document_id,
                parser_profile=str(state.get("parser_profile") or state.get("parser_lane") or "page_index"),
                parser_version=str(state.get("parser_version") or "llm-wiki-durable-frontier-v1"),
                llm_provider=(str(state["llm_provider"]) if state.get("llm_provider") else None),
                llm_model=(str(state["llm_model"]) if state.get("llm_model") else None),
                model_version=(str(state["model_version"]) if state.get("model_version") else None),
                prompt_version=(str(state["prompt_version"]) if state.get("prompt_version") else None),
                # A generation is only stable when this bounded invocation
                # consumed the final frontier item.  Intermediate commits are
                # durable evidence, but must remain visibly expandable until
                # the final ParseView activation.
                status="stable" if not remaining_frontier else "expanding",
            )
        member = ParseGenerationMember(
            member_id=member_id,
            generation_id=session.generation_id,
            workspace_id=session.workspace_id,
            source_document_id=session.source_document_id,
            source_revision_id=session.source_revision_id,
            revision_document_id=session.revision_document_id,
            region=selected.region,
            depth=selected.depth,
            semantic_fingerprint=semantic_fingerprint,
            payload={"frontier_id": selected.frontier_id},
        )
        commit = ParseGenerationCommit(
            commit_id=commit_id,
            generation_id=session.generation_id,
            workspace_id=session.workspace_id,
            source_document_id=session.source_document_id,
            source_revision_id=session.source_revision_id,
            member_ids=(member_id,),
            consumed_frontier_ids=(selected.frontier_id,),
        )
        overlapping_member_ids = tuple(
            item.member_id
            for item in (current_view.selections if current_view is not None else ())
            if item.region.start_char < selected.region.end_char
            and selected.region.start_char < item.region.end_char
        )
        active_fingerprints: dict[str, str] = {}
        if current_view is not None:
            for selection in current_view.selections:
                active_generation = generation_store.get(selection.generation_id)
                if active_generation is None:
                    continue
                active_fingerprints.update(
                    {
                        item.member_id: item.semantic_fingerprint
                        for item in active_generation[2]
                        if item.semantic_fingerprint
                    }
                )
        reconciliation = decide_parse_reconciliation(
            overlapping_member_ids=overlapping_member_ids,
            same_fingerprint=(
                len(overlapping_member_ids) == 1
                and bool(semantic_fingerprint)
                and active_fingerprints.get(overlapping_member_ids[0]) == semantic_fingerprint
            ),
        )
        member = member.model_copy(
            update={
                "payload": {
                    **dict(member.payload),
                    "reconciliation": reconciliation.model_dump(mode="json"),
                }
            }
        )
        retained = ()
        expected_view_version: int | None = None
        predecessor_view_id: str | None = None
        next_view_version = 1
        if current_view is not None:
            retained = tuple(
                item
                for item in current_view.selections
                if item.region.end_char <= selected.region.start_char
                or item.region.start_char >= selected.region.end_char
            )
            expected_view_version = current_view.view_version
            predecessor_view_id = current_view.view_id
            next_view_version = current_view.view_version + 1
        view: ParseView | None = None
        if stable:
            # A generation can be committed over several bounded worker
            # invocations.  Activate the view only after the final item, and
            # include every committed member so earlier chunks remain visible.
            committed_members: dict[str, ParseGenerationMember] = {}
            stored_generation = ParseGenerationStore(
                self.engines.conversation.meta_sqlite,
                workspace_id=ctx.workspace_id,
            ).get(session.generation_id)
            if stored_generation is not None:
                committed_members.update(
                    {item.member_id: item for item in stored_generation[2]}
                )
            committed_members[member.member_id] = member
            new_selections = tuple(
                ParseViewSelection(
                    member_id=item.member_id,
                    generation_id=item.generation_id,
                    region=item.region,
                )
                for item in sorted(
                    committed_members.values(),
                    key=lambda item: (
                        item.region.start_char,
                        item.region.end_char,
                        item.member_id,
                    ),
                )
            )
            view = ParseView(
                view_id=str(
                    stable_id(
                        "kogwistar_llm_wiki.parse_view",
                        session.source_document_id,
                        session.source_revision_id,
                        commit_id,
                    )
                ),
                view_version=next_view_version,
                workspace_id=session.workspace_id,
                source_document_id=session.source_document_id,
                source_revision_id=session.source_revision_id,
                revision_document_id=session.revision_document_id,
                selections=retained + new_selections,
                predecessor_view_id=predecessor_view_id,
            )
        next_session = session.model_copy(
            update={
                "parser_calls": session.parser_calls + 1,
                "last_progress_at": datetime.now(UTC),
            }
        )
        if reconciliation.requires_review:
            return {
                "session": next_session.model_dump(mode="json"),
                "frontier": [item.model_dump(mode="json") for item in remaining_frontier],
                "consumed_frontier_ids": [selected.frontier_id],
                "stable": stable,
                "generation": generation.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
                "members": [member.model_dump(mode="json")],
                "reconciliation": reconciliation.model_dump(mode="json"),
                "diagnostics": {
                    "phase": "parsed_graph_persisted",
                    "reason": "reconciliation_review_required",
                },
            }
        return {
            "session": next_session.model_dump(mode="json"),
            "frontier": [item.model_dump(mode="json") for item in remaining_frontier],
            "consumed_frontier_ids": [selected.frontier_id],
            "stable": stable,
            "generation": generation.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
            "members": [member.model_dump(mode="json")],
            **({"parse_view": view.model_dump(mode="json")} if view is not None else {}),
            "expected_view_version": expected_view_version,
            "reconciliation": reconciliation.model_dump(mode="json"),
            "diagnostics": {
                "phase": "parsed_graph_persisted" if stable else "parse_expanding",
                "frontier_id": selected.frontier_id,
                "remaining_frontier_count": len(remaining_frontier),
            },
        }

    @staticmethod
    def _validate_reparse_region_coverage(
        current_view: ParseView | None,
        selected_region: SourceRegion,
    ) -> None:
        """Prevent a reparse from dropping an only-partially-covered member."""

        if current_view is None:
            return
        partially_replaced = tuple(
            item.member_id
            for item in current_view.selections
            if item.region.start_char < selected_region.end_char
            and selected_region.start_char < item.region.end_char
            and (
                item.region.start_char < selected_region.start_char
                or item.region.end_char > selected_region.end_char
            )
        )
        if partially_replaced:
            raise ValueError(
                "targeted reparse must cover every overlapping active generation member; "
                "select the existing member or expand the target region"
            )

    @staticmethod
    def _offset_extraction_spans(extraction: GraphExtractionWithIDs, offset: int) -> None:
        """Translate region-local parser spans back to immutable-document offsets."""

        if offset == 0:
            return
        for entity in [*(extraction.nodes or []), *(extraction.edges or [])]:
            for mention in getattr(entity, "mentions", ()) or ():
                spans = list(getattr(mention, "spans", ()) or ())
                mention.spans = [
                    span.model_copy(
                        update={
                            "start_char": span.start_char + offset,
                            "end_char": span.end_char + offset,
                        }
                    )
                    for span in spans
                ]

    def _parse_seeded_document(self, ctx: MaintenanceJobExecutionContext) -> Mapping[str, object]:
        """Parse and persist a source-map-seeded document without a transaction around the LLM call."""
        source_document_id = str(ctx.payload.get("source_document_id") or "")
        revision_document_id = str(
            ctx.payload.get("revision_document_id") or source_document_id
        )
        ns = WorkspaceNamespaces(ctx.workspace_id)
        with _temporary_namespace(self.engines.kg, ns.source_space):
            document = self.engines.kg.read.get_document(revision_document_id)
        metadata = dict(document.metadata or {})
        request = IngestPipelineRequest(
            workspace_id=ctx.workspace_id,
            source_uri=str(metadata.get("source_uri") or source_document_id),
            title=str(metadata.get("title") or source_document_id),
            raw_text=str(document.content or ""),
            source_format=str(metadata.get("source_format") or "text"),
            operation_mode="parse_first",
            parser_mode=str(metadata.get("parser_mode") or "heuristic"),
            parser_lane=str(metadata.get("parser_lane") or "page_index"),
            promotion_mode=str(metadata.get("promotion_mode") or "pending"),
            llm_provider=(str(metadata["llm_provider"]) if metadata.get("llm_provider") else None),
            llm_model=(str(metadata["llm_model"]) if metadata.get("llm_model") else None),
        )
        pipeline = IngestPipeline(self.engines)
        accepted_candidate = self.engines.conversation.jobs.accepted_candidate(ctx.job)
        accepted_extraction = None
        if isinstance(accepted_candidate, dict) and accepted_candidate.get("kind") == "document_parse":
            raw_extraction = accepted_candidate.get("extraction")
            if isinstance(raw_extraction, dict):
                accepted_extraction = GraphExtractionWithIDs.model_validate(raw_extraction)
                self._emit_trace(
                    "maintenance_accepted_candidate_reused",
                    workspace_id=ctx.workspace_id,
                    source_document_id=source_document_id,
                    job_id=ctx.job_id,
                    candidate_kind="document_parse",
                )
        # This is an in-memory parser-cache transaction, not a database
        # transaction: the long LLM call never holds a graph connection open.
        with parser_llm_cache_transaction() as parser_cache_transaction:
            parse_result = None
            extraction = accepted_extraction
            if extraction is None:
                parse_result = pipeline.parse_source(
                    request=request,
                    source_document_id=revision_document_id,
                )
                extraction = pipeline.translate_parse_result(
                    parse_result=parse_result,
                    source_document_id=revision_document_id,
                )
                candidate = {
                    "kind": "document_parse",
                    "source_document_id": source_document_id,
                    "extraction": extraction.model_dump(mode="json"),
                }
                decision = self.engines.conversation.jobs.accept_candidate(ctx.job, candidate)
                if decision.get("status") == "rejected":
                    self._emit_trace(
                        "maintenance_repeated_work_discarded",
                        workspace_id=ctx.workspace_id,
                        source_document_id=source_document_id,
                        job_id=ctx.job_id,
                        reason=str(decision.get("reason") or "claim_not_valid"),
                    )
                    return {
                        "node_count": 0,
                        "edge_count": 0,
                        "llm_call_count": int(dict(getattr(parse_result, "usage_summary", {}) or {}).get("llm_call_count") or 0),
                        "stale_claim": True,
                    }
                if decision.get("status") == "existing":
                    winner = self.engines.conversation.jobs.accepted_candidate(ctx.job)
                    if isinstance(winner, dict) and isinstance(winner.get("extraction"), dict):
                        extraction = GraphExtractionWithIDs.model_validate(winner["extraction"])
                        self._emit_trace(
                            "maintenance_repeated_work_discarded",
                            workspace_id=ctx.workspace_id,
                            source_document_id=source_document_id,
                            job_id=ctx.job_id,
                            reason="candidate_already_accepted",
                        )
            if self._claim_lost.is_set():
                return {
                    "node_count": 0,
                    "edge_count": 0,
                    "llm_call_count": int(dict(getattr(parse_result, "usage_summary", {}) or {}).get("llm_call_count") or 0),
                    "stale_claim": True,
                    "comparison_result": {"parse_completed": True},
                }
            if parse_result is not None:
                pipeline.create_parse_retry_history(
                    request=request,
                    source_document_id=source_document_id,
                    parse_result=parse_result,
                    namespace=ns.conv_bg,
                )
            pipeline.ingest_parse_result(
                request=request,
                source_document_id=revision_document_id,
                graph_extraction=extraction,
                namespace=ns.conv_fg,
            )
            parser_cache_transaction.promote()
        pipeline.record_source_readiness(
            request=request,
            source_document_id=source_document_id,
            stage="parsed_graph_persisted",
        )
        return {
            "node_count": len(extraction.nodes or []),
            "edge_count": len(extraction.edges or []),
            "llm_call_count": int(
                dict(getattr(parse_result, "usage_summary", {}) or {}).get("llm_call_count") or 0
            ),
        }
