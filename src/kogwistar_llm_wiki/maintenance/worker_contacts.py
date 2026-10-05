"""Bounded, ACL-first contact candidate maintenance scans."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import cast
from itertools import islice

from .maintenance_strategies import MaintenanceWorkerLike
from ..disambiguation.contact_matching import (
    DEFAULT_MAX_FUZZY_NAME_COMPARISONS,
    ContactIdentityObservation,
    discover_contact_match_candidates,
)
from ..disambiguation.service import DisambiguationService
from .maintenance_strategies import MaintenanceJobExecutionContext

_MAX_CONTACT_SCAN_STREAMS = 64
_MAX_CONTACT_SCAN_OBSERVATIONS = 250
_MAX_CONTACT_SCAN_CANDIDATES = 500
_MAX_CONTACT_SCAN_FUZZY_NAME_COMPARISONS = DEFAULT_MAX_FUZZY_NAME_COMPARISONS


class ContactDisambiguationWorkerMixin(MaintenanceWorkerLike):
    def _handle_contact_disambiguation_scan(
        self, ctx: MaintenanceJobExecutionContext
    ) -> None:
        provider = getattr(self, "contact_observation_provider", None)
        authorize_stream = getattr(self, "contact_stream_authorizer", None)
        if not callable(provider) or not callable(authorize_stream):
            raise TypeError(
                "contact disambiguation scan requires an observation provider and stream ACL"
            )
        authorize = cast(Callable[[str, str], bool], authorize_stream)

        payload = dict(ctx.payload)
        if payload.get("workspace_id") != ctx.workspace_id:
            raise ValueError("contact scan workspace does not match its maintenance job")
        raw_stream_ids = payload.get("source_stream_ids")
        if not isinstance(raw_stream_ids, (list, tuple)) or not raw_stream_ids:
            raise ValueError("contact scan requires authorized source_stream_ids")
        if len(raw_stream_ids) > _MAX_CONTACT_SCAN_STREAMS:
            raise ValueError("contact scan exceeds source stream limit")
        stream_ids: list[str] = []
        for stream_id in raw_stream_ids:
            if not isinstance(stream_id, str) or not stream_id.strip():
                raise ValueError("contact scan source_stream_ids must be non-empty strings")
            stream_ids.append(stream_id.strip())
        if len(stream_ids) != len(set(stream_ids)):
            raise ValueError("contact scan source_stream_ids must be unique")

        # Authorize the complete requested scope before allowing a source adapter to read.
        for stream_id in sorted(stream_ids):
            if not authorize(ctx.workspace_id, stream_id):
                raise PermissionError("contact scan source stream is not authorized")
        payload["source_stream_ids"] = tuple(stream_ids)
        observations = provider(ctx.workspace_id, payload)
        if isinstance(observations, (str, bytes)) or not isinstance(observations, Iterable):
            raise TypeError("contact observation provider must return an iterable")
        bounded = tuple(islice(observations, _MAX_CONTACT_SCAN_OBSERVATIONS + 1))
        if len(bounded) > _MAX_CONTACT_SCAN_OBSERVATIONS:
            raise ValueError("contact scan exceeds observation limit")
        if any(not isinstance(item, ContactIdentityObservation) for item in bounded):
            raise TypeError("contact observation provider returned an invalid observation")
        for item in bounded:
            if not authorize(ctx.workspace_id, item.stream_id):
                raise PermissionError("contact scan provider returned an unauthorized stream")

        candidates = discover_contact_match_candidates(
            bounded,
            authorize_stream=authorize,
            max_observations=_MAX_CONTACT_SCAN_OBSERVATIONS,
            max_candidates=_MAX_CONTACT_SCAN_CANDIDATES,
            max_fuzzy_name_comparisons=_MAX_CONTACT_SCAN_FUZZY_NAME_COMPARISONS,
        )
        self._assert_claim_owned(ctx, reason="claim_lost_before_contact_candidate_persist")
        persisted_ids = DisambiguationService(self.engines).persist_contact_candidates(
            candidates,
            authorize_stream=authorize,
        )
        self._assert_claim_owned(ctx, reason="claim_lost_before_contact_scan_ack")
        self._acknowledge_job(ctx)
        self._emit_trace(
            "contact_disambiguation_scan_complete",
            workspace_id=ctx.workspace_id,
            job_id=ctx.job_id,
            authorized_stream_count=len(stream_ids),
            observation_count=len(bounded),
            candidate_count=len(candidates),
            persisted_snapshot_count=len(persisted_ids),
        )


__all__ = ["ContactDisambiguationWorkerMixin"]
