"""Derived-knowledge and execution-wisdom worker steps."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import cast

from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar.json_types import JsonObject
from kogwistar.maintenance.contracts import BeforeWrite
from kogwistar.maintenance.models import MaintenanceTemplateResult
from kogwistar.maintenance.template import run_grouped_maintenance_template
from kogwistar.runtime.models import RunSuccess, StepRunResult
from kogwistar.runtime.runtime import StepContext
from kogwistar.wisdom.template import write_execution_wisdom_artifacts

from ..configuration.workspace import WorkspaceNamespaces
from ..maintenance import operation_category
from ..models import NamespaceEngines
from ..policies.rules import (
    LlmWikiDerivedKnowledgePolicy,
    LlmWikiLifecyclePolicy,
    LlmWikiPolicies,
    LlmWikiWisdomPolicy,
)
from ..utils import _temporary_namespace
from .maintenance_strategies import MaintenanceWorkerLike
from .state import and_where as _and_where

logger = logging.getLogger(__name__)

class DerivedMaintenanceWorkerMixin(MaintenanceWorkerLike):
    """Methods for derived knowledge and execution-history wisdom."""

    def _load_request_node(self, workspace_id: str, req_node_id: str) -> Node | None:
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            nodes = list(
                self.engines.conversation.read.get_nodes(
                    where={
                        "$and": [
                            {"workspace_id": workspace_id},
                            {"id": req_node_id},
                        ]
                    }
                )
            )
        if nodes:
            return nodes[0]
        return None

    def _step_distill(self, ctx: StepContext) -> StepRunResult:
        """Aggregate promoted knowledge into derived-knowledge artifacts."""
        raw_workspace_id = ctx.state_view.get("workspace_id")
        workspace_id = raw_workspace_id if isinstance(raw_workspace_id, str) else None
        deps_raw = ctx.state_view.get("_deps")
        if isinstance(deps_raw, dict):
            raw_engines = deps_raw.get("engines")
            engines: NamespaceEngines | None = (
                raw_engines if isinstance(raw_engines, NamespaceEngines) else None
            )
            before_write = deps_raw.get("before_authoritative_write")
        else:
            engines = deps_raw if isinstance(deps_raw, NamespaceEngines) else None
            before_write = None
        if not workspace_id or not engines:
            logger.error("Missing workspace_id or engines in distillation step context")
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])

        ns = WorkspaceNamespaces(workspace_id)
        maintenance_mode = str(ctx.state_view.get("maintenance_mode") or "request")
        raw_candidates = ctx.state_view.get("maintenance_candidates")
        selected_ids = {
            str(item.get("candidate_id") or "")
            for item in (raw_candidates if isinstance(raw_candidates, list) else [])
            if isinstance(item, Mapping) and str(item.get("candidate_id") or "").strip()
        }
        derived_policy = cast(LlmWikiDerivedKnowledgePolicy, self.policies.derived_knowledge)
        source_where = derived_policy.source_query(workspace_id=workspace_id).where
        if maintenance_mode == "background":
            if not selected_ids:
                return RunSuccess(state_update=[("u", {"distillation_complete": True, "candidate_count": 0})])
            source_where = _and_where(
                cast(dict[str, object], source_where),
                cast(dict[str, object], {"id": {"$in": sorted(selected_ids)}}),
            )
        with _temporary_namespace(engines.kg, ns.curated_kg_space):
            promoted_nodes = list(engines.kg.read.get_nodes(where=source_where))

        if not promoted_nodes:
            self._emit_trace(
                "maintenance_graph_effect",
                workspace_id=str(workspace_id),
                source_document_id=str(ctx.state_view.get("source_document_id") or ""),
                maintenance_kind="distill",
                operation_category="distillation",
                source_node_count=0,
                derived_node_count=0,
            )
            return RunSuccess(state_update=[("u", {"distillation_complete": True})])

        derived_engine = engines.derived_knowledge_engine()
        template_result: MaintenanceTemplateResult = run_grouped_maintenance_template(
            engines.kg,
            target_engine=derived_engine,
            source_namespace=ns.curated_kg_space,
            target_namespace=ns.derived_knowledge,
            source_where=source_where,
            group_key_for_node=derived_policy.group_key,
            match_where_for_group=lambda label: derived_policy.match_where(
                workspace_id=workspace_id,
                label=label,
            ),
            build_node_for_group=lambda label, nodes, existing, created_at_ms: self._build_derived_node_for_group(
                workspace_id=workspace_id,
                label=label,
                nodes=nodes,
                existing=existing,
                created_at_ms=created_at_ms,
                policies=self.policies,
                fallback_span_factory=lambda: Grounding(
                    spans=[
                        Span(
                            collection_page_url=f"conversation/{ns.conv_bg}",
                            document_page_url=f"conversation/{ns.conv_bg}",
                            doc_id=f"conv:{ns.conv_bg}",
                            insertion_method="workflow_trace",
                            page_number=1,
                            start_char=0,
                            end_char=1,
                            excerpt=f"distilled:{label}",
                            context_before="",
                            context_after="",
                chunk_id=None,
                source_cluster_id=None,
                verification=None,
                        )
                    ]
                ),
            ),
            before_write=cast(BeforeWrite[str], before_write) if callable(before_write) else None,
        )
        for result in template_result.grouped_results:
            logger.info(
                "Derived knowledge synthesis for entity '%s' with %s source nodes.",
                result.group_key,
                result.source_node_count,
            )

        self._emit_trace(
            "maintenance_graph_effect",
            workspace_id=str(workspace_id),
            source_document_id=str(ctx.state_view.get("source_document_id") or ""),
            maintenance_kind="distill",
            operation_category=operation_category("distill"),
            source_node_count=sum(result.source_node_count for result in template_result.grouped_results),
            derived_node_count=len(template_result.grouped_results),
            derived_node_ids=list(template_result.emitted_group_keys),
        )
        return RunSuccess(
            state_update=[
                (
                    "u",
                    {
                        "distillation_complete": True,
                        "derived_knowledge_complete": True,
                        "distilled_entities": list(template_result.emitted_group_keys),
                    },
                )
            ]
        )

    def _step_check_done(self, ctx: StepContext) -> StepRunResult:
        """Resolver step that cleanly finalizes derived-knowledge maintenance."""
        raw_workspace_id = ctx.state_view.get("workspace_id")
        workspace_id = raw_workspace_id if isinstance(raw_workspace_id, str) else None
        deps_raw = ctx.state_view.get("_deps")
        if isinstance(deps_raw, dict):
            raw_engines = deps_raw.get("engines")
            engines = raw_engines if isinstance(raw_engines, NamespaceEngines) else None
        else:
            engines = deps_raw if isinstance(deps_raw, NamespaceEngines) else None
        if not workspace_id or not engines:
            logger.error("Missing workspace_id or engines in maintenance completion step context")
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])
        return RunSuccess(state_update=[("u", {"maintenance_complete": True})])

    def _build_derived_node_for_group(
        self,
        *,
        workspace_id: str,
        label: str,
        nodes: list[Node],
        existing: list[Node],
        created_at_ms: int,
        policies: LlmWikiPolicies,
        fallback_span_factory: Callable[[], Grounding],
    ) -> Node:
        raw_mentions: list[Grounding] = []
        for node in nodes:
            if hasattr(node, "mentions") and node.mentions:
                raw_mentions.extend(node.mentions)

        merged_mentions: list[Grounding] = []
        seen_mentions: set[str] = set()
        for mention in raw_mentions:
            try:
                mention_key = mention.model_dump_json()
            except Exception:
                mention_key = str(mention)
            if mention_key not in seen_mentions:
                merged_mentions.append(mention)
                seen_mentions.add(mention_key)
        if not merged_mentions:
            merged_mentions = [fallback_span_factory()]

        source_node_ids = sorted(str(node.id) for node in nodes)
        return Node(
            id=str(stable_id("derived_knowledge", workspace_id, label, *source_node_ids)),
            label=label,
            type="entity",
            summary=f"Derived knowledge synthesis for {label} aggregated from {len(nodes)} source documents.",
            domain_id=None,
            canonical_entity_id=None,
            properties=None,
            embedding=None,
            doc_id=None,
            level_from_root=None,
            mentions=merged_mentions,
            metadata=cast(JsonObject, cast(LlmWikiDerivedKnowledgePolicy, policies.derived_knowledge).build_metadata(
                workspace_id=workspace_id,
                label=label,
                source_node_ids=source_node_ids,
                replaces_ids=cast(LlmWikiLifecyclePolicy, policies.lifecycle).replacement_ids(
                    [*cast(Sequence[Node | str], existing)]
                ),
                created_at_ms=created_at_ms,
            )),
        )

    def _emit_execution_wisdom_from_history(
        self,
        workspace_id: str,
        engines: NamespaceEngines,
        *,
        before_write: BeforeWrite[object] | None = None,
    ) -> list[str]:
        """Analyze completed execution history and emit execution-derived wisdom."""
        if not workspace_id or not engines:
            return []

        ns = WorkspaceNamespaces(workspace_id)
        wisdom_policy = cast(LlmWikiWisdomPolicy, self.policies.wisdom)
        lifecycle_policy = cast(LlmWikiLifecyclePolicy, self.policies.lifecycle)
        result_items = write_execution_wisdom_artifacts(
            engines.conversation,
            target_engine=engines.wisdom,
            source_namespace=ns.conv_bg,
            target_namespace=ns.wisdom,
            source_where=wisdom_policy.source_query(workspace_id=workspace_id).where,
            min_failure_signals=wisdom_policy.min_failure_signals,
            match_where_for_pattern=lambda pattern: wisdom_policy.match_where(
                workspace_id=workspace_id,
                step_op=pattern.step_op,
            ),
            build_node_for_pattern=lambda pattern, existing, created_at_ms: Node(
                id=str(
                    stable_id(
                        "execution_wisdom",
                        workspace_id,
                        pattern.step_op,
                        *sorted(str(node.id) for node in pattern.failure_nodes),
                    )
                ),
                label=f"execution_failure_pattern:{pattern.step_op}",
                type="entity",
                summary=(
                    f"Repeated failure pattern detected for workflow step '{pattern.step_op}' "
                    f"({len(pattern.failure_nodes)} occurrences across {len(pattern.run_ids)} runs). "
                    "Investigate step resolver, input contract, or upstream data quality."
                ),
                domain_id=None,
                canonical_entity_id=None,
                properties=None,
                embedding=None,
                doc_id=None,
                level_from_root=None,
                mentions=[
                    Grounding(
                        spans=[
                            Span(
                                collection_page_url=f"conversation/{ns.conv_bg}",
                                document_page_url=f"conversation/{ns.conv_bg}",
                                doc_id=f"conv:{ns.conv_bg}",
                                insertion_method="execution_history",
                                page_number=1,
                                start_char=0,
                                end_char=1,
                                excerpt=f"failure_pattern:{pattern.step_op} n={len(pattern.failure_nodes)}",
                                context_before="",
                                context_after="",
                                chunk_id=None,
                                source_cluster_id=None,
                                verification=None,
                            )
                        ]
                    )
                ],
                metadata=cast(JsonObject, wisdom_policy.build_metadata(
                    workspace_id=workspace_id,
                    step_op=pattern.step_op,
                    failure_count=len(pattern.failure_nodes),
                    evidence_run_ids=list(pattern.run_ids),
                    replaces_ids=lifecycle_policy.replacement_ids(
                        [*cast(Sequence[Node | str], existing)]
                    ),
                    created_at_ms=created_at_ms,
                )
                | {"label": f"execution_failure_pattern:{pattern.step_op}"}),
            ),
            before_write=before_write,
        )

        emitted = [result.step_op for result in result_items]
        for result in result_items:
            logger.info(
                "Emitted execution_wisdom for step_op='%s' (failures=%s, runs=%s)",
                result.step_op,
                result.failure_count,
                len(result.run_ids),
            )
        return emitted

    def derive_problem_solving_wisdom_from_history(self, ctx: StepContext) -> StepRunResult:
        """Resolver wrapper for workflow-native execution-wisdom extraction."""
        workspace_id = ctx.state_view.get("workspace_id")
        deps_raw = ctx.state_view.get("_deps")
        engines = deps_raw.get("engines") if isinstance(deps_raw, dict) else deps_raw
        before_write = deps_raw.get("before_authoritative_write") if isinstance(deps_raw, dict) else None
        if not isinstance(workspace_id, str) or not isinstance(engines, NamespaceEngines):
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])
        emitted = self._emit_execution_wisdom_from_history(
            workspace_id,
            engines,
            before_write=cast(BeforeWrite[object], before_write) if callable(before_write) else None,
        )
        return RunSuccess(
            state_update=[("u", {"history_wisdom_complete": True, "execution_wisdom_emitted": emitted})]
        )

    def _step_distill_from_history(self, ctx: StepContext) -> StepRunResult:
        """Compatibility alias for older workflow step names."""
        return self.derive_problem_solving_wisdom_from_history(ctx)

    def _step_noop(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for terminal/noop nodes."""
        return RunSuccess(state_update=[])
