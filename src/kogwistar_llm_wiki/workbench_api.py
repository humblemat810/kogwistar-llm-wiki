"""Transport-neutral workbench API adapter.

HTTP, CLI, and local desktop callers can share this adapter.  It intentionally
contains no web framework and delegates all graph access to ``IngestPipeline``.
"""

from __future__ import annotations

import time
import inspect
import threading
import uuid
from typing import Any, Callable, Mapping, cast

from .ingest_pipeline import IngestPipeline
from .investigation_history import InvestigationHistoryRecord
from .namespaces import GraphSpace
from .maintenance_patch_apply import apply_maintenance_patch_for_scope
from .maintenance_patches import MaintenancePatch
from .semantic_lens import InvestigationOutcome, SemanticLensRequest, SemanticLensSnapshot, validate_edit_proposal
from .workbench import KnowledgeWorkbench, WorkbenchMode
from .workbench_cockpit import CockpitResponder, WorkbenchCockpit, validate_cockpit_proposal
from .workbench_background import (
    CodexWorkbenchDispatcher,
    CodexWorkbenchWorker,
    ProgressCallback,
    WorkbenchInteraction,
    WorkbenchInteractionStore,
)
from .multimodal_remote import RepresentationServiceUnavailable
from .settings import SettingsService
from .compose_config import ComposeOptions, check_compose_text, render_compose, validate_options
from .model_catalog import available_models

AgentResponder = Callable[[SemanticLensRequest, SemanticLensSnapshot], str]
ProgressAgentResponder = Callable[[SemanticLensRequest, SemanticLensSnapshot, ProgressCallback], str]


class WorkbenchApi:
    def __init__(
        self,
        pipeline: IngestPipeline,
        *,
        agent_responder: AgentResponder | ProgressAgentResponder | None = None,
        cockpit_responder: CockpitResponder | None = None,
        codex_worker_count: int = 0,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        settings_path: str | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.settings = SettingsService(pipeline, path=settings_path)
        self.agent_responder = agent_responder
        self.cockpit_responder = cockpit_responder
        self.interactions = WorkbenchInteractionStore(pipeline.engines)
        self._confirmation_locks: dict[tuple[str, str], threading.Lock] = {}
        self._confirmation_locks_guard = threading.Lock()
        self.dispatcher: CodexWorkbenchDispatcher | None = None
        if codex_worker_count > 0:
            if agent_responder is None and cockpit_responder is None:
                raise ValueError("Codex background workers require an agent_responder or cockpit_responder")
            worker = CodexWorkbenchWorker(
                pipeline.engines,
                execute_turn=self._execute_background_turn,
                trace_sink=trace_sink,
            )
            self.dispatcher = CodexWorkbenchDispatcher(worker, worker_count=codex_worker_count)

    def readiness(self) -> dict[str, object]:
        """Check that owned engines are open and SQL backends accept a probe."""
        engines = self.pipeline.engines
        if getattr(engines, "_closed", False):
            return {"ready": False, "service": "kogwistar-llm-wiki", "reason": "engines_closed"}
        checks: dict[str, str] = {}
        try:
            for name in ("conversation", "workflow", "kg", "wisdom", "derived_knowledge"):
                engine = getattr(engines, name, None)
                if engine is None:
                    continue
                backend = getattr(engine, "backend", None)
                sql_engine = getattr(backend, "engine", None)
                if sql_engine is not None and hasattr(sql_engine, "connect"):
                    with sql_engine.connect() as connection:
                        connection.exec_driver_sql("SELECT 1")
                    checks[name] = "ok"
                else:
                    checks[name] = "open"
            multimodal = getattr(self.pipeline, "multimodal_encoder", None)
            if multimodal is not None and hasattr(multimodal, "readiness"):
                snapshot = multimodal.readiness()
                checks["multimodal_representation"] = "ok" if snapshot.get("ready") else "degraded"
        except Exception as exc:  # noqa: BLE001
            return {"ready": False, "service": "kogwistar-llm-wiki", "checks": checks, "reason": str(exc)}
        return {"ready": True, "service": "kogwistar-llm-wiki", "checks": checks}

    def get_settings(self, *, workspace_id: str = "default") -> dict[str, object]:
        return self.settings.snapshot(workspace_id=workspace_id)

    def settings_health(self, *, workspace_id: str = "default") -> dict[str, object]:
        return self.settings.health(workspace_id=workspace_id, readiness=self.readiness())

    def update_desired_settings(
        self, *, workspace_id: str, changes: Mapping[str, object]
    ) -> dict[str, object]:
        return self.settings.update_desired(changes, workspace_id=workspace_id)

    def apply_settings(
        self, *, workspace_id: str, confirmed: bool
    ) -> dict[str, object]:
        return self.settings.apply(workspace_id=workspace_id, confirmed=confirmed)

    def compose_preview(self, payload: Mapping[str, Any]) -> dict[str, object]:
        """Return a generated Compose bundle without writing files or secrets."""
        options = ComposeOptions(
            backend=str(payload.get("backend") or "postgres"),
            workspace=str(payload.get("workspace_id") or "default"),
            project_name=str(payload.get("project_name") or "llm-wiki"),
            mode=str(payload.get("mode") or "gpu"),
            with_otel=bool(payload.get("with_otel", False)),
            with_oauth=bool(payload.get("with_oauth", False)),
            auth_mode=str(payload.get("auth_mode") or "disabled"),
            model_revision=str(payload.get("model_revision") or ""),
            representation_dimension=int(payload.get("representation_dimension") or 1024),
        )
        errors = validate_options(options)
        return {"valid": not errors, "errors": errors, "yaml": render_compose(options) if not errors else None}

    @staticmethod
    def compose_check(payload: Mapping[str, Any]) -> dict[str, object]:
        text = payload.get("yaml")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("yaml must be a non-empty string")
        return check_compose_text(text)

    @staticmethod
    def available_models(role: str, *, provider: str | None = None, base_url: str | None = None) -> dict[str, object]:
        if role not in {"parser", "maintenance"}:
            raise ValueError("role must be parser or maintenance")
        return available_models(role, provider=provider, base_url=base_url)

    def get_lens(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request = _lens_request(payload)
        result = self.pipeline.resolve_semantic_lens(request).to_dict()
        multimodal = self._multimodal_route(payload, query_text=request.query_text)
        if multimodal is not None:
            result["multimodal"] = multimodal
        return result

    def _multimodal_route(
        self,
        payload: Mapping[str, Any],
        *,
        query_text: str,
    ) -> dict[str, object] | None:
        """Add an optional bounded multimodal route without changing graph truth."""
        if payload.get("include_multimodal", True) is False or not query_text.strip():
            return None
        if not self.settings.snapshot().get("effective", {}).get("multimodal", {}).get("enabled", True):
            return {"status": "disabled", "route": "multimodal_projection", "hits": []}
        if (
            self.pipeline.multimodal_projection_store is None
            or self.pipeline.multimodal_encoder is None
        ):
            return None
        limit = max(1, min(int(payload.get("multimodal_limit") or 10), 100))
        try:
            hits = self.pipeline.search_multimodal(query_text, limit=limit)
        except RepresentationServiceUnavailable as exc:
            return {
                "status": "degraded",
                "route": "multimodal_projection",
                "reason": str(exc),
                "hits": [],
            }
        except Exception as exc:  # keep canonical graph query available on route errors
            return {
                "status": "error",
                "route": "multimodal_projection",
                "reason": str(exc),
                "hits": [],
            }
        return {
            "status": "ok",
            "route": "multimodal_projection",
            "profile_fingerprint": self.pipeline.multimodal_encoder.profile.fingerprint,
            "hits": [
                {
                    "view_id": hit.view_id,
                    "score": hit.score,
                    "source_id": hit.source_id,
                    "source_revision_id": hit.source_revision_id,
                    "modality": hit.modality,
                    "locator": hit.locator,
                    "metadata": {**hit.metadata, "grounding": "source_view"},
                }
                for hit in hits
            ],
        }

    def ask(
        self,
        payload: Mapping[str, Any],
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, object]:
        """Run one grounded user turn through deterministic or injected-agent mode."""
        request = _lens_request(payload)
        mode = _workbench_mode(payload.get("mode"))
        session_id = str(payload.get("session_id") or "default")
        workbench = KnowledgeWorkbench(
            lens_service=self.pipeline.semantic_lens_service,
            history_service=self.pipeline.investigation_history_service,
            clock_ms=lambda: int(time.time() * 1000),
        )
        responder = None
        agent_status = "not_requested"
        if mode == "codex":
            if self.cockpit_responder is not None:
                requested_interaction_id = str(payload.get("interaction_id") or "")
                if requested_interaction_id:
                    existing = self.interactions.get(
                        workspace_id=request.workspace_id,
                        interaction_id=requested_interaction_id,
                    )
                    if existing is not None and existing.status in {"completed", "failed"}:
                        if existing.response is not None:
                            return existing.response
                        raise RuntimeError(existing.error or "existing cockpit interaction failed")
                response = self._ask_cockpit(request=request, session_id=session_id, progress=progress or _ignore_progress)
                interaction_id = requested_interaction_id or str(uuid.uuid4())
                response["interaction_id"] = interaction_id
                stored_interaction, created = self.interactions.persist_result(
                    workspace_id=request.workspace_id,
                    interaction_id=interaction_id,
                    session_id=session_id,
                    submitted_at_ms=int(payload.get("submitted_at_ms") or int(time.time() * 1000)),
                    response=response,
                )
                if not created and stored_interaction.response is not None:
                    return stored_interaction.response
                return response
            if self.agent_responder is None:
                agent_status = "not_configured"
            else:
                agent_status = "active"

                def answer_from_agent(snapshot: SemanticLensSnapshot) -> str:
                    return _invoke_agent_responder(
                        self.agent_responder,
                        request,
                        snapshot,
                        progress or _ignore_progress,
                    )

                responder = answer_from_agent
        turn = workbench.ask(
            request=request,
            session_id=session_id,
            mode=mode,
            agent_answer=responder,
        )
        response = {
            "mode": mode,
            "agent_status": agent_status,
            "answer": {
                "text": turn.answer.answer,
                "outcome": turn.answer.outcome,
                "lens_id": turn.answer.lens_id,
                "source_watermark": turn.answer.source_watermark,
                "cited_entity_ids": list(turn.answer.cited_entity_ids),
                "insufficiency_reason": turn.answer.insufficiency_reason,
            },
            "snapshot": turn.snapshot.to_dict(),
            "history": _history_record(turn.history),
        }
        multimodal = self._multimodal_route(payload, query_text=request.query_text)
        if multimodal is not None:
            response["multimodal"] = multimodal
        return response

    def _ask_cockpit(
        self,
        *,
        request: SemanticLensRequest,
        session_id: str,
        progress: ProgressCallback,
    ) -> dict[str, object]:
        assert self.cockpit_responder is not None
        cockpit = WorkbenchCockpit(
            resolve_lens=self.pipeline.resolve_semantic_lens,
            query_history=lambda workspace_id, target_session_id, limit: self.get_history(
                workspace_id=workspace_id,
                session_id=target_session_id,
                limit=limit,
            ),
        )
        result = cockpit.run(
            request=request,
            session_id=session_id,
            responder=self.cockpit_responder,
            progress=progress,
        )
        snapshot = cockpit.last_snapshot
        if snapshot is None:
            raise RuntimeError("cockpit completed without a final lens snapshot")
        outcome = result
        history = self.pipeline.investigation_history_service.record(
            workspace_id=request.workspace_id,
            session_id=session_id,
            question=request.query_text,
            action_kind="codex_cockpit",
            snapshot=snapshot,
            outcome=InvestigationOutcome(
                outcome=outcome.outcome,
                session_id=session_id,
                lens_id=snapshot.lens_id,
                source_watermark=snapshot.source_watermark,
                cited_entity_ids=tuple(outcome.cited_entity_ids),
                proposal=outcome.proposal,
            ),
            created_at_ms=int(time.time() * 1000),
        )
        return {
            "mode": "codex",
            "agent_status": "cockpit_active",
            "answer": {
                "text": outcome.answer,
                "outcome": outcome.outcome,
                "lens_id": snapshot.lens_id,
                "source_watermark": snapshot.source_watermark,
                "cited_entity_ids": outcome.cited_entity_ids,
                "proposal": outcome.proposal,
            },
            "snapshot": snapshot.to_dict(),
            "history": _history_record(history),
            "cockpit_trace": outcome.trace,
            "cockpit_observations": outcome.observations,
            "proposal_request": outcome.final_request,
        }

    def submit_interaction(self, payload: Mapping[str, Any]) -> dict[str, object]:
        """Persist and schedule one Codex turn without blocking the HTTP caller."""
        if self.dispatcher is None or (self.agent_responder is None and self.cockpit_responder is None):
            raise RuntimeError("Codex background worker is not configured")
        validated = _lens_request(payload)
        stored_payload = {**dict(payload), "workspace_id": validated.workspace_id, "mode": "codex"}
        interaction = self.interactions.enqueue(
            stored_payload,
            max_retries=int(payload.get("max_retries") or 3),
        )
        self.dispatcher.notify(interaction.workspace_id)
        return interaction.to_dict()

    def get_interaction(self, *, workspace_id: str, interaction_id: str) -> dict[str, object] | None:
        interaction = self.interactions.get(workspace_id=workspace_id, interaction_id=interaction_id)
        return None if interaction is None else interaction.to_dict()

    def recover_interactions(self, workspace_id: str) -> None:
        if self.dispatcher is None:
            raise RuntimeError("Codex background worker is not configured")
        self.dispatcher.recover(workspace_id)

    def close(self) -> None:
        if self.dispatcher is not None:
            self.dispatcher.close()

    def _execute_background_turn(
        self,
        payload: Mapping[str, object],
        progress: ProgressCallback,
    ) -> Mapping[str, object]:
        progress()
        response = self.ask(payload, progress=progress)
        response["interaction_id"] = str(payload["interaction_id"])
        progress()
        return response

    def get_history(
        self,
        *,
        workspace_id: str,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        records = self.pipeline.query_investigation_history(
            workspace_id=workspace_id,
            session_id=session_id,
            limit=limit,
        )
        return [
            {
                "id": record.id,
                "workspace_id": record.workspace_id,
                "session_id": record.session_id,
                "lens_id": record.lens_id,
                "source_watermark": record.source_watermark,
                "question": record.question,
                "action_kind": record.action_kind,
                "outcome": record.outcome,
                "cited_entity_ids": list(record.cited_entity_ids),
                "proposal": record.proposal,
                "insufficiency_reason": record.insufficiency_reason,
                "created_at_ms": record.created_at_ms,
            }
            for record in records
        ]

    def validate_proposal(self, payload: Mapping[str, Any]) -> dict[str, object]:
        request_payload = payload.get("request")
        proposal = payload.get("proposal")
        if not isinstance(request_payload, Mapping) or not isinstance(proposal, Mapping):
            return {"accepted": False, "requires_confirmation": True, "reason": "invalid_proposal_request"}
        request = _lens_request(request_payload)
        snapshot = self.pipeline.resolve_semantic_lens(request)
        if isinstance(proposal.get("maintenance_patch"), dict):
            accepted, reason = validate_cockpit_proposal(
                snapshot,
                dict(proposal),
                expected_workspace_id=request.workspace_id,
            )
            return {
                "accepted": accepted,
                "requires_confirmation": True,
                "reason": reason,
                "target_ids": list(proposal.get("target_ids") or ()),
                "lens_id": snapshot.lens_id,
            }
        validation = validate_edit_proposal(snapshot, proposal)
        return {
            "accepted": validation.accepted,
            "requires_confirmation": validation.requires_confirmation,
            "reason": validation.reason,
            "target_ids": list(validation.target_ids),
            "lens_id": snapshot.lens_id,
        }

    def confirm_cockpit_proposal(self, payload: Mapping[str, Any]) -> dict[str, object]:
        workspace_id = str(payload.get("workspace_id") or "")
        interaction_id = str(payload.get("interaction_id") or "")
        if not workspace_id or not interaction_id:
            return {"status": "rejected", "reason": "interaction_confirmation_required"}
        interaction = self.interactions.get(workspace_id=workspace_id, interaction_id=interaction_id)
        if interaction is None or interaction.status != "completed" or not interaction.response:
            return {"status": "rejected", "reason": "completed_interaction_not_found"}
        prior_confirmation = self.interactions.get_confirmation(
            workspace_id=workspace_id,
            interaction_id=interaction_id,
        )
        if prior_confirmation is not None:
            return prior_confirmation
        with self._confirmation_lock(workspace_id, interaction_id):
            return self._confirm_cockpit_proposal_locked(
                payload=payload,
                workspace_id=workspace_id,
                interaction_id=interaction_id,
                interaction=interaction,
            )

    def _confirm_cockpit_proposal_locked(
        self,
        *,
        payload: Mapping[str, Any],
        workspace_id: str,
        interaction_id: str,
        interaction: WorkbenchInteraction,
    ) -> dict[str, object]:
        prior_confirmation = self.interactions.get_confirmation(
            workspace_id=workspace_id,
            interaction_id=interaction_id,
        )
        if prior_confirmation is not None:
            return prior_confirmation
        stored_response = interaction.response
        assert stored_response is not None
        stored_answer = stored_response.get("answer")
        stored_proposal = stored_answer.get("proposal") if isinstance(stored_answer, Mapping) else None
        stored_request = stored_response.get("proposal_request")
        if not isinstance(stored_proposal, dict) or not isinstance(stored_request, Mapping):
            return {"status": "rejected", "reason": "stored_proposal_not_found"}
        supplied_proposal = payload.get("proposal")
        if supplied_proposal is not None and supplied_proposal != stored_proposal:
            return {"status": "rejected", "reason": "proposal_does_not_match_interaction"}
        request = _lens_request(stored_request)
        snapshot = self.pipeline.resolve_semantic_lens(request)
        valid, reason = validate_cockpit_proposal(
            snapshot,
            stored_proposal,
            expected_workspace_id=workspace_id,
        )
        if not valid:
            return {"status": "rejected", "reason": reason, "lens_id": snapshot.lens_id}
        if not bool(payload.get("confirmed", False)):
            return {"status": "confirmation_required", "reason": reason, "lens_id": snapshot.lens_id}
        patch = MaintenancePatch.model_validate(stored_proposal["maintenance_patch"])
        expected_revisions = stored_proposal.get("expected_revisions")
        result = apply_maintenance_patch_for_scope(
            self.pipeline.engines,
            patch,
            expected_revisions=expected_revisions if isinstance(expected_revisions, Mapping) else None,
        )
        confirmation_outcome = "proposal_applied" if result.status.value == "applied" else "proposal_rejected"
        response = {
            "status": result.status.value,
            "patch_id": result.patch_id,
            "artifact_id": result.artifact_id,
            "applied_count": result.applied_count,
            "skipped_count": result.skipped_count,
            "failed_count": result.failed_count,
            "validation": result.validation.model_dump(mode="json"),
            "lens": self.pipeline.resolve_semantic_lens(request).to_dict(),
        }
        stored_confirmation, created = self.interactions.persist_confirmation(
            workspace_id=workspace_id,
            interaction_id=interaction_id,
            payload=response,
        )
        if not created:
            return stored_confirmation
        self.pipeline.investigation_history_service.record(
            workspace_id=workspace_id,
            session_id=interaction.session_id,
            question=str(stored_request.get("query_text") or ""),
            action_kind="codex_cockpit_confirmation",
            snapshot=snapshot,
            outcome=InvestigationOutcome(
                outcome=confirmation_outcome,
                session_id=interaction.session_id,
                lens_id=snapshot.lens_id,
                source_watermark=snapshot.source_watermark,
                cited_entity_ids=tuple(str(value) for value in stored_proposal.get("evidence_ids") or ()),
                proposal=stored_proposal,
            ),
            created_at_ms=int(time.time() * 1000),
        )
        return response

    def _confirmation_lock(self, workspace_id: str, interaction_id: str) -> threading.Lock:
        key = (workspace_id, interaction_id)
        with self._confirmation_locks_guard:
            return self._confirmation_locks.setdefault(key, threading.Lock())


def _lens_request(payload: Mapping[str, Any]) -> SemanticLensRequest:
    return SemanticLensRequest(
        workspace_id=str(payload["workspace_id"]),
        graph_spaces=tuple(payload.get("graph_spaces") or (GraphSpace.CURATED_KG.value,)),
        query_text=str(payload.get("query_text") or ""),
        semantic_retrieval=bool(payload.get("semantic_retrieval", False)),
        explicit_anchor_ids=tuple(str(value) for value in (payload.get("explicit_anchor_ids") or ())),
        hop_limit=int(payload.get("hop_limit", 1)),
        max_nodes=int(payload.get("max_nodes", 40)),
        max_edges=int(payload.get("max_edges", 80)),
        max_hyperedges=int(payload.get("max_hyperedges", 12)),
        pinned_node_ids=tuple(str(value) for value in (payload.get("pinned_node_ids") or ())),
        source_watermark=payload.get("source_watermark"),
        include_tombstones=bool(payload.get("include_tombstones", False)),
    )


def _workbench_mode(value: object) -> WorkbenchMode:
    mode = str(value or "deterministic")
    if mode not in {"deterministic", "codex"}:
        raise ValueError("mode must be deterministic or codex")
    return cast(WorkbenchMode, mode)


def _history_record(record: InvestigationHistoryRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "session_id": record.session_id,
        "lens_id": record.lens_id,
        "outcome": record.outcome,
        "created_at_ms": record.created_at_ms,
    }


def _invoke_agent_responder(
    responder: AgentResponder | ProgressAgentResponder,
    request: SemanticLensRequest,
    snapshot: SemanticLensSnapshot,
    progress: ProgressCallback,
) -> str:
    """Keep legacy two-argument listeners while enabling live progress."""
    try:
        parameters = inspect.signature(responder).parameters.values()
        accepts_progress = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters) or sum(
            parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
            for parameter in parameters
        ) >= 3
    except (TypeError, ValueError):
        accepts_progress = False
    if accepts_progress:
        return cast(ProgressAgentResponder, responder)(request, snapshot, progress)
    return cast(AgentResponder, responder)(request, snapshot)


def _ignore_progress() -> None:
    return


__all__ = ["WorkbenchApi"]
