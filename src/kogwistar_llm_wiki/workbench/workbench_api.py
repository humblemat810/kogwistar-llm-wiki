"""Transport-neutral workbench API adapter.

HTTP, CLI, and local desktop callers can share this adapter.  It intentionally
contains no web framework and delegates all graph access to ``IngestPipeline``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import inspect
import json
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from ..compose.options import ComposeOptions, validate_options
from ..compose.rendering import render_compose
from ..compose.validation import check_compose_text
from ..configuration.settings_service import SettingsService
from ..configuration.workspace import GraphSpace
from ..disambiguation.contact_book import (
    ContactObservationProvider,
    build_address_book_projection,
)
from ..disambiguation.contact_matching import (
    ContactIdentityObservation,
    contact_evidence_snapshot_id,
    contact_match_basis,
)
from ..disambiguation.disambiguation_contracts import DisambiguationDecisionKind
from ..disambiguation.service import DisambiguationService
from ..embeddings.multimodal_remote import EmbeddingServiceUnavailable
from ..ingest_pipeline import IngestPipeline
from ..maintenance.crosslink_reviews import CrosslinkGroupReviewService
from ..maintenance.maintenance_patch_apply import apply_maintenance_patch_for_scope
from ..maintenance.maintenance_patches import MaintenancePatch
from ..memory import MemoryService
from ..providers.model_catalog import available_models
from .investigation_history import InvestigationHistoryRecord
from .semantic_lens import (
    InvestigationOutcome,
    SemanticLensRequest,
    SemanticLensSnapshot,
    validate_edit_proposal,
)
from .workbench import KnowledgeWorkbench, WorkbenchMode
from .workbench_background import (
    CodexWorkbenchDispatcher,
    CodexWorkbenchWorker,
    ProgressCallback,
    WorkbenchInteraction,
    WorkbenchInteractionStore,
)
from .workbench_cockpit import (
    CockpitResponder,
    WorkbenchCockpit,
    validate_cockpit_proposal,
)

AgentResponder = Callable[[SemanticLensRequest, SemanticLensSnapshot], str]
ProgressAgentResponder = Callable[[SemanticLensRequest, SemanticLensSnapshot, ProgressCallback], str]

_MAX_CONTACT_PAGE_SIZE = 1000
_MAX_CONTACT_SNAPSHOT_ITEMS = 5000
_MAX_CONTACT_CURSOR_CHARS = 4096


def _contact_snapshot_fingerprint(items: Sequence[Mapping[str, object]]) -> str:
    payload = json.dumps(
        items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _encode_contact_cursor(
    *, workspace_id: str, kind: str, snapshot_fingerprint: str, offset: int
) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "workspace": hashlib.sha256(workspace_id.encode("utf-8")).hexdigest(),
            "kind": kind,
            "snapshot": snapshot_fingerprint,
            "offset": offset,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_contact_cursor(
    token: str | None,
    *,
    workspace_id: str,
    kind: str,
    snapshot_fingerprint: str,
    item_count: int,
) -> int:
    if token is None:
        return 0
    if not isinstance(token, str) or not token or len(token) > _MAX_CONTACT_CURSOR_CHARS:
        raise ValueError("contact directory cursor is invalid")
    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("contact directory cursor is invalid") from exc
    expected = {
        "v": 1,
        "workspace": hashlib.sha256(workspace_id.encode("utf-8")).hexdigest(),
        "kind": kind,
        "snapshot": snapshot_fingerprint,
    }
    if not isinstance(payload, Mapping) or any(
        payload.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("contact directory cursor is stale or does not match request")
    offset = payload.get("offset")
    if type(offset) is not int or not 0 <= offset <= item_count:
        raise ValueError("contact directory cursor is invalid")
    return offset


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
        resource_authorizer: Callable[[str, str, str, str], bool] | None = None,
        contact_authorize_stream: Callable[[str, str], bool] | None = None,
        contact_observation_provider: ContactObservationProvider | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.codex_memory = MemoryService(pipeline.engines)
        self.settings = SettingsService(
            pipeline,
            path=settings_path,
            codex_memory=self.codex_memory,
        )
        self.agent_responder = agent_responder
        self.cockpit_responder = cockpit_responder
        self._resource_authorizer = resource_authorizer
        self._contact_authorize_stream = contact_authorize_stream or (
            lambda _workspace_id, _stream_id: False
        )
        self._contact_acl_configured = contact_authorize_stream is not None
        self._contact_observation_provider = contact_observation_provider
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

    def authorize_resource(
        self, workspace_id: str, resource_type: str, resource_id: str, action: str
    ) -> bool:
        """Fail closed unless the host supplies an application resource ACL."""
        authorizer = self._resource_authorizer
        return bool(
            authorizer
            and authorizer(workspace_id, resource_type, resource_id, action)
        )

    def list_crosslink_group_reviews(self, *, workspace_id: str, limit: int = 100) -> dict[str, object]:
        """List pending provider-generated cross-link groups for a workspace."""
        groups = CrosslinkGroupReviewService(self.pipeline.engines).list_pending(
            workspace_id=workspace_id, limit=limit
        )
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "groups": [
                {
                    "artifact_id": str(group.id),
                    "label": group.label,
                    "summary": group.summary,
                    "metadata": dict(group.metadata or {}),
                }
                for group in groups
            ],
        }

    def decide_crosslink_group_reviews(
        self,
        *,
        workspace_id: str,
        decisions: Sequence[Mapping[str, object]],
        actor_id: str,
        authority_claims: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Record independent, version-checked decisions for a bounded batch."""
        results = CrosslinkGroupReviewService(self.pipeline.engines).decide_batch(
            workspace_id=workspace_id,
            decisions=decisions,
            actor_id=actor_id,
            authority_claims=authority_claims,
        )
        return {"status": "ok", "workspace_id": workspace_id, "results": results}

    def list_contact_matches(
        self, *, workspace_id: str, limit: int = 100, cursor: str | None = None
    ) -> dict[str, object]:
        """Page a bounded, ACL-visible snapshot of contact review suggestions."""
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if type(limit) is not int or not 1 <= limit <= _MAX_CONTACT_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 1000")
        service = DisambiguationService(self.pipeline.engines)
        candidates = service.list_current_contact_candidates(
            workspace_id=workspace_id,
            authorize_stream=self._contact_authorize_stream,
            limit=_MAX_CONTACT_SNAPSHOT_ITEMS,
        )
        observations = (
            self._load_authorized_contact_observations(workspace_id)
            if candidates and self._contact_acl_configured
            else {}
        )
        results: list[dict[str, object]] = []
        for candidate in candidates:
            result = candidate.model_dump(mode="json")
            pair = [
                observations[entity_id]
                for entity_id in candidate.entity_ids
                if entity_id in observations
            ]
            if len(pair) == 2 and contact_match_basis(pair[0], pair[1]) != candidate.metadata.get(
                "match_basis"
            ):
                continue
            current = len(pair) == 2 and contact_evidence_snapshot_id(
                pair[0], pair[1], basis=str(candidate.metadata.get("match_basis") or "")
            ) == candidate.evidence_snapshot_id
            result["evidence_available"] = current
            result["evidence_stale"] = bool(pair) and not current
            result["evidence"] = [
                {
                    "entity_id": item.entity_id,
                    "stream_id": item.stream_id,
                    "display_names": list(item.display_names),
                    "contact_points": [point.model_dump(mode="json") for point in item.contact_points],
                    "source_document_ids": list(item.source_document_ids),
                    "evidence_revision_ids": list(item.evidence_revision_ids),
                }
                for item in pair
            ] if current else []
            results.append(result)
        fingerprint = _contact_snapshot_fingerprint(results)
        offset = _decode_contact_cursor(
            cursor,
            workspace_id=workspace_id,
            kind="matches",
            snapshot_fingerprint=fingerprint,
            item_count=len(results),
        )
        page = results[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "candidate_window_may_be_incomplete": (
                len(candidates) >= _MAX_CONTACT_SNAPSHOT_ITEMS
            ),
            "snapshot_id": fingerprint,
            "next_cursor": (
                _encode_contact_cursor(
                    workspace_id=workspace_id,
                    kind="matches",
                    snapshot_fingerprint=fingerprint,
                    offset=next_offset,
                )
                if next_offset < len(results)
                else None
            ),
            "results": page,
        }

    def decide_contact_match(
        self,
        *,
        workspace_id: str,
        candidate_key: str,
        evidence_snapshot_id: str,
        expected_evidence_version: int,
        decision: str,
        confirmed: bool,
        actor_id: str | None = None,
    ) -> dict[str, object]:
        """Record confirmed identity judgment; never apply a graph patch here."""
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if (
            not isinstance(candidate_key, str)
            or len(candidate_key) != 78
            or not candidate_key.startswith("contact-match:")
            or any(char not in "0123456789abcdef" for char in candidate_key[14:])
        ):
            raise ValueError("candidate_key must be a contact-match SHA-256 identifier")
        if not isinstance(evidence_snapshot_id, str) or not evidence_snapshot_id.strip():
            raise ValueError("evidence_snapshot_id must not be empty")
        if type(expected_evidence_version) is not int or expected_evidence_version < 0:
            raise ValueError("expected_evidence_version must be non-negative")
        if decision not in {
            DisambiguationDecisionKind.SAME_ENTITY.value,
            DisambiguationDecisionKind.DISTINCT_ENTITIES.value,
        }:
            raise ValueError("decision must be same_entity or distinct_entities")
        if confirmed is not True:
            raise ValueError("confirmed must be true to record a contact decision")
        if not self._contact_acl_configured:
            raise PermissionError("contact stream authorization is not configured")

        with self._confirmation_lock(workspace_id, f"contact:{candidate_key}"):
            service = DisambiguationService(self.pipeline.engines)
            candidates = service.list_current_contact_candidates(
                workspace_id=workspace_id,
                authorize_stream=self._contact_authorize_stream,
                limit=_MAX_CONTACT_SNAPSHOT_ITEMS,
            )
            candidate = next((item for item in candidates if item.candidate_key == candidate_key), None)
            if candidate is None:
                return {"status": "not_found", "workspace_id": workspace_id}
            if self._contact_observation_provider is None:
                return {"status": "unavailable", "workspace_id": workspace_id,
                        "reason": "current_contact_evidence_provider_not_configured"}
            observations = self._load_authorized_contact_observations(workspace_id)
            pair = [observations[item] for item in candidate.entity_ids if item in observations]
            current = len(pair) == 2 and contact_evidence_snapshot_id(
                pair[0], pair[1], basis=str(candidate.metadata.get("match_basis") or "")
            ) == candidate.evidence_snapshot_id
            if not current:
                return {"status": "stale", "workspace_id": workspace_id,
                        "candidate_key": candidate_key,
                        "current_evidence_version": candidate.last_reconciled_evidence_version,
                        "reason": "source_evidence_changed_or_unavailable"}
            if candidate.evidence_snapshot_id != evidence_snapshot_id or (
                candidate.last_reconciled_evidence_version != expected_evidence_version
            ):
                return {"status": "stale", "workspace_id": workspace_id,
                        "candidate_key": candidate_key,
                        "current_evidence_snapshot_id": candidate.evidence_snapshot_id,
                        "current_evidence_version": candidate.last_reconciled_evidence_version}
            result = service.record_user_answer(
                candidate,
                evidence_version=expected_evidence_version + 1,
                decision=DisambiguationDecisionKind(decision),
                actor_id=actor_id,
            )
        return {"status": "recorded", "workspace_id": workspace_id,
                "candidate_key": candidate_key, "evidence_snapshot_id": evidence_snapshot_id,
                "evidence_version": result.reconciliation.new_evidence_version,
                "decision": result.decision_kind, "decision_node_id": result.decision_node_id,
                "patch_id": result.patch_id, "patch_intent": result.patch_intent,
                "graph_patch_status": "not_applied"}

    def _load_authorized_contact_observations(
        self, workspace_id: str
    ) -> dict[str, ContactIdentityObservation]:
        provider = self._contact_observation_provider
        if provider is None:
            return {}
        observations = provider(
            workspace_id, _MAX_CONTACT_SNAPSHOT_ITEMS, self._contact_authorize_stream
        )
        if (
            not isinstance(observations, Sequence)
            or len(observations) > _MAX_CONTACT_SNAPSHOT_ITEMS
        ):
            raise ValueError("contact observation provider must return a bounded sequence")
        by_entity: dict[str, ContactIdentityObservation] = {}
        for observation in observations:
            if not isinstance(observation, ContactIdentityObservation):
                raise TypeError("contact observation provider returned an invalid observation")
            if observation.workspace_id != workspace_id:
                raise ValueError("contact observation provider returned another workspace")
            if not self._contact_authorize_stream(workspace_id, observation.stream_id):
                raise PermissionError("contact observation source stream is not authorized")
            if observation.entity_id in by_entity:
                raise ValueError("contact observation provider returned duplicate entities")
            by_entity[observation.entity_id] = observation
        return by_entity

    def list_address_book(
        self, *, workspace_id: str, limit: int = 500, cursor: str | None = None
    ) -> dict[str, object]:
        """Page complete, resolved address-book groups from one bounded snapshot."""
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if type(limit) is not int or not 1 <= limit <= _MAX_CONTACT_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 1000")
        provider = self._contact_observation_provider
        if provider is None or not self._contact_acl_configured:
            return {"status": "unavailable", "workspace_id": workspace_id,
                    "reason": "authorized contact observation provider is not configured",
                    "results": []}
        observations = provider(
            workspace_id, _MAX_CONTACT_SNAPSHOT_ITEMS, self._contact_authorize_stream
        )
        if (
            not isinstance(observations, Sequence)
            or len(observations) > _MAX_CONTACT_SNAPSHOT_ITEMS
        ):
            raise ValueError("contact observation provider exceeded limit")
        decisions = DisambiguationService(self.pipeline.engines).list_current_contact_candidates(
            workspace_id=workspace_id,
            authorize_stream=self._contact_authorize_stream,
            limit=_MAX_CONTACT_SNAPSHOT_ITEMS,
        )
        entries = build_address_book_projection(
            observations, decisions,
            authorize_stream=self._contact_authorize_stream,
            max_observations=_MAX_CONTACT_SNAPSHOT_ITEMS,
            max_decisions=_MAX_CONTACT_SNAPSHOT_ITEMS,
        )
        results = [
            {
                "contact_id": entry.contact_id,
                "entity_ids": list(entry.entity_ids),
                "display_names": list(entry.display_names),
                "contact_points": [
                    {
                        "point": claim.point.model_dump(mode="json"),
                        "stream_id": claim.stream_id,
                        "entity_id": claim.entity_id,
                        "source_document_ids": list(claim.source_document_ids),
                        "evidence_revision_ids": list(claim.evidence_revision_ids),
                        "observed_at_ms": claim.observed_at_ms,
                    }
                    for claim in entry.contact_points
                ],
            }
            for entry in entries
        ]
        fingerprint = _contact_snapshot_fingerprint(results)
        offset = _decode_contact_cursor(
            cursor,
            workspace_id=workspace_id,
            kind="address_book",
            snapshot_fingerprint=fingerprint,
            item_count=len(results),
        )
        page = results[offset : offset + limit]
        next_offset = offset + len(page)
        return {"status": "ok", "workspace_id": workspace_id,
                "observation_window_may_be_incomplete": (
                    len(observations) >= _MAX_CONTACT_SNAPSHOT_ITEMS
                    or len(decisions) >= _MAX_CONTACT_SNAPSHOT_ITEMS
                ),
                "snapshot_id": fingerprint,
                "next_cursor": (
                    _encode_contact_cursor(
                        workspace_id=workspace_id,
                        kind="address_book",
                        snapshot_fingerprint=fingerprint,
                        offset=next_offset,
                    )
                    if next_offset < len(results)
                    else None
                ),
                "results": page}

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
                checks["multimodal_embedding"] = "ok" if snapshot.get("ready") else "degraded"
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

    def recall_memory(
        self,
        *,
        workspace_id: str,
        query_text: str = "",
        include_inferred: bool = True,
        limit: int | None = None,
        authorized_stream_ids: Sequence[str] | None = None,
    ) -> dict[str, object]:
        return self.codex_memory.recall(
            workspace_id=workspace_id,
            query_text=query_text,
            include_inferred=include_inferred,
            limit=limit,
            authorized_stream_ids=authorized_stream_ids,
        )

    def capture_memory(
        self, payload: Mapping[str, object] | list[Mapping[str, object]]
    ) -> dict[str, object]:
        return self.codex_memory.capture(payload)

    def review_memory(
        self,
        *,
        workspace_id: str,
        kind: str | None = None,
        confidence: str | None = None,
        lifecycle_status: str | None = None,
        limit: int = 50,
        authorized_stream_ids: Sequence[str] | None = None,
    ) -> dict[str, object]:
        return self.codex_memory.review(
            workspace_id=workspace_id,
            kind=kind,
            confidence=confidence,
            lifecycle_status=lifecycle_status,
            limit=limit,
            authorized_stream_ids=authorized_stream_ids,
        )

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
            embedding_dimension=int(payload.get("embedding_dimension") or 1024),
            embedding_max_model_len=int(payload.get("embedding_max_model_len") or 8192),
            embedding_crop_token_budget=int(payload.get("embedding_crop_token_budget") or 7680),
            embedding_gpu_memory_utilization=float(payload.get("embedding_gpu_memory_utilization") or 0.86),
            embedding_vllm_enforce_eager=bool(payload.get("embedding_vllm_enforce_eager", True)),
            embedding_vllm_max_num_seqs=int(payload.get("embedding_vllm_max_num_seqs") or 1),
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
        except EmbeddingServiceUnavailable as exc:
            return {
                "status": "degraded",
                "route": "multimodal_projection",
                "reason": str(exc),
                "hits": [],
            }
        except Exception as exc:  # noqa: BLE001 - keep canonical graph query available on route errors
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
    def integer(name: str, default: int) -> int:
        value = payload.get(name)
        return default if value is None else int(value)

    return SemanticLensRequest(
        workspace_id=str(payload["workspace_id"]),
        graph_spaces=tuple(payload.get("graph_spaces") or (GraphSpace.CURATED_KG.value,)),
        query_text=str(payload.get("query_text") or ""),
        semantic_retrieval=bool(payload.get("semantic_retrieval", False)),
        explicit_anchor_ids=tuple(str(value) for value in (payload.get("explicit_anchor_ids") or ())),
        hop_limit=integer("hop_limit", 1),
        max_nodes=integer("max_nodes", 40),
        max_edges=integer("max_edges", 80),
        max_hyperedges=integer("max_hyperedges", 12),
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
