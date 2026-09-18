"""Request-scoped maintenance candidate selection and audit persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping

from kogwistar.engine_core.models import Node
from kogwistar.id_provider import stable_id

from ..configuration.workspace import WorkspaceNamespaces
from ..utils import _temporary_namespace
from .maintenance_selection import select_request_candidates
from .maintenance_strategies import MaintenanceJobExecutionContext
from .state import belongs_to_workspace as _belongs_to_workspace
from .state import edge_ids as _edge_ids


class MaintenanceSelectionWorkerMixin:
    """Select bounded request candidates without changing execution budgets."""

    def _attach_request_selection(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Enrich the in-flight payload without changing budgets or history."""
        kg = getattr(self.engines, "kg", None)
        if kg is None or not callable(getattr(getattr(kg, "read", None), "get_nodes", None)):
            return
        seed_ids = {str(item) for item in (ctx.payload.get("seed_node_ids") or []) if item}
        continuation = ctx.payload.get("maintenance_context")
        if isinstance(continuation, Mapping):
            turns = continuation.get("turns")
            if isinstance(turns, list) and turns:
                last_turn = turns[-1]
                if isinstance(last_turn, Mapping):
                    seed_ids.update(
                        str(item)
                        for item in (last_turn.get("next_seed_node_ids") or [])
                        if str(item).strip()
                    )
            seed_ids.update(
                str(item)
                for item in (continuation.get("compressed_node_ids") or [])
                if str(item).strip()
            )
        if ctx.request_node_id:
            seed_ids.add(ctx.request_node_id)
        ns = WorkspaceNamespaces(ctx.workspace_id)
        try:
            nodes: list[Node] = []
            edges: list[object] = []
            for namespace in (ns.curated_kg_space, ns.source_space):
                with _temporary_namespace(self.engines.kg, namespace):
                    nodes.extend(self.engines.kg.read.get_nodes(limit=250))
                    edges.extend(self.engines.kg.read.get_edges(limit=500))
            nodes = [node for node in nodes if _belongs_to_workspace(node, ctx.workspace_id)]
            node_ids = {
                str(getattr(node, "safe_get_id", lambda node=node: getattr(node, "id", ""))() or "")
                for node in nodes
            }
            edges = [edge for edge in edges if _edge_ids(edge) <= node_ids]
        except Exception as exc:  # noqa: BLE001 - selection is advisory; guarded work remains authoritative
            self._emit_trace(
                "maintenance_selection_degraded",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
            )
            return
        seeds = [node for node in nodes if str(getattr(node, "safe_get_id", lambda: "")()) in seed_ids]
        topic = str(ctx.payload.get("topic") or "").strip().lower()
        if topic:
            terms = {term for term in topic.split() if len(term) > 2}
            for node in nodes:
                node_id = str(getattr(node, "safe_get_id", lambda: "")())
                metadata_value = getattr(node, "metadata", {})
                searchable = " ".join(
                    [
                        str(getattr(node, "label", "") or ""),
                        str(getattr(node, "summary", "") or ""),
                        str(metadata_value if isinstance(metadata_value, Mapping) else ""),
                    ]
                ).lower()
                if node_id and terms and any(term in searchable for term in terms) and node not in seeds:
                    seeds.append(node)
        if not seeds and ctx.request_node is not None:
            seeds = [ctx.request_node]
        selected = select_request_candidates(seeds, nodes, edges, max_candidates=24)
        ctx.payload["maintenance_candidates"] = [item.as_dict() for item in selected]
        ctx.payload["selection_strategy"] = "connected_semantic_evidence_history"
        self._persist_selection_audit(ctx)
        self._emit_trace(
            "maintenance_candidates_selected",
            workspace_id=ctx.workspace_id,
            job_id=ctx.job_id,
            selection_strategy=ctx.payload["selection_strategy"],
            candidates=ctx.payload["maintenance_candidates"],
        )

    def _persist_selection_audit(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Persist selection metadata in the workspace maintenance lane."""
        candidates = list(ctx.payload.get("maintenance_candidates") or [])
        if not candidates:
            return
        ns = WorkspaceNamespaces(ctx.workspace_id)
        audit_key = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance.maintenance_selection",
                ctx.workspace_id,
                ctx.job_id,
                ctx.payload.get("selection_strategy") or "",
                json.dumps(candidates, sort_keys=True, separators=(",", ":")),
            )
        )
        try:
            with _temporary_namespace(self.engines.conversation, ns.conv_bg):
                self.engines.conversation.send_lane_message(
                    conversation_id=f"maintenance:{ctx.request_node_id}",
                    inbox_id="inbox:worker:maintenance:audit",
                    sender_id="lane:worker:maintenance",
                    recipient_id="lane:worker:maintenance-audit",
                    msg_type="maintenance.selection",
                    purpose="internal",
                    payload={
                        "workspace_id": ctx.workspace_id,
                        "job_id": ctx.job_id,
                        "mode": str(ctx.payload.get("mode") or "request"),
                        "topic": str(ctx.payload.get("topic") or ""),
                        "selection_strategy": ctx.payload.get("selection_strategy"),
                        "candidates": candidates,
                    },
                    idempotency_key=audit_key,
                )
        except Exception as exc:  # noqa: BLE001 - audit failure must not bypass maintenance fences
            self._emit_trace(
                "maintenance_selection_audit_failed",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
            )
