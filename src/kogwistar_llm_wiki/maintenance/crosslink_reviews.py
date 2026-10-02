"""Durable human decisions for independently reviewed cross-link groups."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar.server.auth_middleware import can_access_security_scope
from kogwistar.utils import source_pointer_has_character_span, validate_source_pointer

from ..configuration.workspace import WorkspaceNamespaces
from ..maintenance.maintenance_guards import source_digest
from ..maintenance.maintenance_patches import MaintenancePatch
from ..maintenance.state import metadata_mapping as _metadata_mapping
from ..models import NamespaceEngines
from ..utils import _background_namespace, _temporary_namespace
from .crosslink_proposals import CrosslinkReviewDecision


class CrosslinkReviewConflict(RuntimeError):
    """A review decision lost its expected-version compare-and-swap."""


class CrosslinkGroupReviewService:
    """Store group evaluations in conversation graph and decisions via Kogwistar CAS."""

    __slots__ = ("engines",)

    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def list_pending(self, *, workspace_id: str, limit: int = 100) -> list[Node]:
        if not workspace_id.strip() or not 1 <= limit <= 500:
            raise ValueError("workspace_id and a limit from 1 to 500 are required")
        ns = WorkspaceNamespaces(workspace_id)
        with _background_namespace(self.engines.conversation, ns.conv_bg):
            nodes = self.engines.conversation.read.get_nodes(
                where={
                    "artifact_kind": "crosslink_group_review",
                    "workspace_id": workspace_id,
                    "review_status": "pending",
                },
                limit=limit,
            )
        result: list[Node] = []
        for node in nodes:
            if (
                self._decision(workspace_id, str(node.id)) is None
                and self._has_current_read_access(workspace_id, node)
            ):
                result.append(node)
        return result

    def decide_batch(
        self,
        *,
        workspace_id: str,
        decisions: Sequence[CrosslinkReviewDecision | Mapping[str, object]],
        actor_id: str,
        authority_claims: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        if not workspace_id.strip() or not actor_id.strip():
            raise ValueError("workspace_id and actor_id are required")
        if not 1 <= len(decisions) <= 100:
            raise ValueError("batch must contain between 1 and 100 independent decisions")
        normalized = [CrosslinkReviewDecision.model_validate(item) for item in decisions]
        if len({item.artifact_id for item in normalized}) != len(normalized):
            raise ValueError("a batch may decide each group only once")
        outcomes: list[dict[str, object]] = []
        for decision in normalized:
            try:
                outcomes.append(self._decide_one(workspace_id, actor_id, decision, authority_claims))
            except (CrosslinkReviewConflict, KeyError, ValueError) as exc:
                outcomes.append({
                    "artifact_id": decision.artifact_id,
                    "status": "conflict" if isinstance(exc, CrosslinkReviewConflict) else "rejected",
                    "error": str(exc),
                })
        return outcomes

    def _decide_one(
        self,
        workspace_id: str,
        actor_id: str,
        decision: CrosslinkReviewDecision,
        authority_claims: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        ns = WorkspaceNamespaces(workspace_id)
        with _background_namespace(self.engines.conversation, ns.conv_bg):
            matches = self.engines.conversation.read.get_nodes(ids=[decision.artifact_id], limit=1)
        if not matches:
            raise KeyError(f"cross-link review artifact not found: {decision.artifact_id}")
        artifact = matches[0]
        metadata = _metadata_mapping(artifact)
        if (
            metadata.get("artifact_kind") != "crosslink_group_review"
            or metadata.get("workspace_id") != workspace_id
            or metadata.get("review_status") != "pending"
        ):
            raise ValueError("artifact is not a pending cross-link review in this workspace")
        if not self._has_current_read_access(workspace_id, artifact):
            raise PermissionError("cross-link evidence is outside the current security scope")
        current = self._decision(workspace_id, decision.artifact_id)
        if current is not None:
            if current.get("decision") != decision.decision:
                raise CrosslinkReviewConflict("this cross-link group already has the opposite decision")
            self._persist_decision_event(current)
            if decision.decision == "approve":
                self._enqueue_revalidation(workspace_id, metadata, authority_claims)
            return {"artifact_id": decision.artifact_id, "status": "already_decided", "decision": decision.decision}
        if decision.expected_version != int(metadata.get("decision_version") or 1):
            raise CrosslinkReviewConflict("cross-link review artifact version is stale")
        event = {
            "workspace_id": workspace_id,
            "artifact_id": decision.artifact_id,
            "decision": decision.decision,
            "expected_version": decision.expected_version,
            "actor_id": actor_id,
        }
        projection_ns = f"ws:{workspace_id}:projection_state"
        projection_key = self._projection_key(decision.artifact_id)
        inserted = self.engines.conversation.meta_sqlite.compare_and_swap_named_projection(
            projection_ns,
            projection_key,
            event,
            expected_last_authoritative_seq=None,
            expected_last_materialized_seq=None,
            last_authoritative_seq=decision.expected_version,
            last_materialized_seq=decision.expected_version,
            projection_schema_version=1,
            materialization_status="ready",
        )
        if not inserted:
            winner = self._decision(workspace_id, decision.artifact_id)
            if winner is None or winner.get("decision") != decision.decision:
                raise CrosslinkReviewConflict("another reviewer committed a decision first")
        self._persist_decision_event(event)
        if decision.decision == "approve":
            self._enqueue_revalidation(workspace_id, metadata, authority_claims)
        return {"artifact_id": decision.artifact_id, "status": "decided", "decision": decision.decision}

    def _has_current_read_access(self, workspace_id: str, artifact: Node) -> bool:
        """Recheck current source and endpoint ACLs before exposing a review payload."""
        metadata = _metadata_mapping(artifact)
        if metadata.get("workspace_id") != workspace_id:
            return False
        try:
            patch = MaintenancePatch.model_validate(
                json.loads(str(metadata.get("patch_json") or ""))
            )
        except (TypeError, ValueError):
            return False
        if patch.scope.workspace_id != workspace_id:
            return False

        endpoint_ids = {
            str(value)
            for operation in patch.operations
            for value in (operation.from_node_id, operation.to_node_id)
            if value
        }
        pointers = [
            pointer
            for operation in patch.operations
            if operation.provenance is not None
            for pointer in operation.provenance.source_pointers
        ]
        document_ids = {
            str(pointer.get("doc_id") or "").strip()
            for pointer in pointers
        }
        if not endpoint_ids or not pointers or "" in document_ids:
            return False

        namespaces = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.kg, namespaces.curated_kg_space):
            nodes = self.engines.kg.read.get_nodes(
                ids=sorted(endpoint_ids), limit=len(endpoint_ids)
            )
            superseded_ids = {
                str(operation.properties.get("supersedes_edge_id") or "").strip()
                for operation in patch.operations
                if operation.properties.get("supersedes_edge_id")
            }
            edges = (
                self.engines.kg.read.get_edges(ids=sorted(superseded_ids), limit=len(superseded_ids))
                if superseded_ids
                else []
            )
        if {str(node.id) for node in nodes} != endpoint_ids:
            return False
        if {str(edge.id) for edge in edges} != superseded_ids:
            return False
        for entity in [*nodes, *edges]:
            entity_metadata = _metadata_mapping(entity)
            if str(entity_metadata.get("workspace_id") or "") != workspace_id:
                return False
            scope = str(
                entity_metadata.get("acl_scope")
                or entity_metadata.get("security_scope")
                or ""
            ).strip()
            if scope and not can_access_security_scope(scope):
                return False

        with _temporary_namespace(self.engines.kg, namespaces.source_space):
            for document_id in sorted(document_ids):
                try:
                    document = self.engines.kg.read.get_document(document_id)
                except (KeyError, ValueError):
                    return False
                source_metadata = _metadata_mapping(document)
                logical_source_id = str(
                    source_metadata.get("logical_source_document_id")
                    or source_metadata.get("source_document_id")
                    or ""
                )
                revision_id = str(
                    source_metadata.get("source_revision_id")
                    or source_metadata.get("revision_id")
                    or ""
                )
                if (
                    str(source_metadata.get("workspace_id") or "") != workspace_id
                    or str(
                        source_metadata.get("revision_document_id")
                        or source_metadata.get("source_revision_document_id")
                        or ""
                    ) != document_id
                ):
                    return False
                scope = str(
                    source_metadata.get("acl_scope")
                    or source_metadata.get("security_scope")
                    or ""
                ).strip()
                if scope and not can_access_security_scope(scope):
                    return False
                text = str(document.content or "")
                digest = source_digest(text)
                if str(source_metadata.get("source_digest") or "") != digest:
                    return False
                for pointer in pointers:
                    if str(pointer.get("doc_id") or "").strip() != document_id:
                        continue
                    if (
                        str(pointer.get("workspace_id") or workspace_id) != workspace_id
                        or str(pointer.get("source_document_id") or "") != logical_source_id
                        or str(pointer.get("source_revision_id") or "") != revision_id
                        or str(pointer.get("source_digest") or "") != digest
                    ):
                        return False
                    if not source_pointer_has_character_span(pointer):
                        return False
                    try:
                        validate_source_pointer(
                            pointer,
                            source_text_by_cluster={
                                document_id: text,
                                str(pointer.get("source_cluster_id") or ""): text,
                            },
                            end_mode="exclusive",
                            require_source_text=True,
                            require_text_match=True,
                        )
                    except ValueError:
                        return False
        return True

    def _decision(self, workspace_id: str, artifact_id: str) -> dict[str, object] | None:
        row = self.engines.conversation.meta_sqlite.get_named_projection(
            f"ws:{workspace_id}:projection_state", self._projection_key(artifact_id)
        )
        payload = row.get("payload") if row else None
        return dict(payload) if isinstance(payload, Mapping) else None

    @staticmethod
    def _projection_key(artifact_id: str) -> str:
        return f"crosslink_review_decision:{artifact_id}"

    def _persist_decision_event(self, event: Mapping[str, object]) -> None:
        workspace_id = str(event["workspace_id"])
        artifact_id = str(event["artifact_id"])
        node = Node(
            id=str(stable_id("crosslink_review_decision", workspace_id, artifact_id)),
            label=f"Cross-link review decision: {event['decision']}",
            type="entity",
            summary=f"Reviewer {event['actor_id']} selected {event['decision']}.",
            doc_id=artifact_id,
            mentions=[Grounding(spans=[Span.from_dummy_for_workflow(artifact_id)])],
            metadata={
                "artifact_kind": "crosslink_group_decision",
                **dict(event),
            },
        )
        with _background_namespace(self.engines.conversation, WorkspaceNamespaces(workspace_id).conv_bg):
            self.engines.conversation.write.add_node(node)

    def _enqueue_revalidation(
        self,
        workspace_id: str,
        metadata: Mapping[str, object],
        authority_claims: Mapping[str, object] | None = None,
    ) -> None:
        patch = json.loads(str(metadata.get("patch_json") or "{}"))
        raw_fences = json.loads(str(metadata.get("source_revision_fences") or "[]"))
        if not isinstance(raw_fences, list) or not raw_fences:
            raise ValueError("pending group lacks its pinned source revision fences")
        fences = [
            {
                "source_document_id": str(item.get("source_document_id") or ""),
                "source_revision_id": str(item.get("source_revision_id") or ""),
                "revision_document_id": str(item.get("revision_document_id") or ""),
                "source_digest": str(item.get("source_digest") or ""),
            }
            for item in raw_fences
            if isinstance(item, Mapping)
        ]
        if not fences or any(not all(item.values()) for item in fences):
            raise ValueError("pending group has an incomplete source revision fence")
        source_document_id = fences[0]["source_document_id"]
        source_revision_id = fences[0]["source_revision_id"]
        source_digest = fences[0]["source_digest"]
        revision_document_id = fences[0]["revision_document_id"]
        group_id = str(metadata.get("group_id") or "")
        job_id = str(stable_id("crosslink_human_approval_job", workspace_id, group_id))
        payload: dict[str, object] = {
            "workspace_id": workspace_id,
            "request_node_id": job_id,
            "maintenance_kind": "document_validate_crosslinks",
            "mode": "background",
            "crosslink_candidate_group_id": group_id,
            "patch": patch,
            "source_document_id": source_document_id,
            "source_revision_id": source_revision_id,
            "source_digest": source_digest,
            "revision_document_id": revision_document_id,
            "source_revision_fences": fences,
            "required_stage": "parsed_graph_persisted",
            "accepted_confidence": 1.0,
            "seed_node_ids": [],
            "maintenance_plan": ["document_validate_crosslinks"],
            "maintenance_phase_index": 0,
        }
        for key in (
            "crosslink_parse_quality",
            "parse_quality_status",
            "source_region_status",
            "source_view_status",
        ):
            if metadata.get(key) is not None:
                payload[key] = metadata[key]
        if authority_claims:
            payload["authority_claims"] = dict(authority_claims)
            payload["authority_required"] = True
        self.engines.conversation.jobs.enqueue(
            job_id=job_id,
            namespace=WorkspaceNamespaces(workspace_id).maintenance_jobs,
            entity_kind="maintenance_job",
            entity_id=source_document_id,
            job_kind="maintenance_job:document_validate_crosslinks",
            op="UPSERT",
            payload=payload,
        )


__all__ = ["CrosslinkGroupReviewService", "CrosslinkReviewConflict"]
