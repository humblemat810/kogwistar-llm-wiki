from __future__ import annotations

import time

from kogwistar.engine_core.models import Grounding, Node, Span

from kogwistar_llm_wiki import (
    IngestPipeline,
    SemanticLensRequest,
    SemanticLensSnapshot,
    WorkbenchApi,
    WorkspaceNamespaces,
    build_in_memory_namespace_engines,
)
from kogwistar_llm_wiki.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from kogwistar_llm_wiki.maintenance_patch_apply import apply_maintenance_patch_for_scope
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.workbench_cockpit import (
    CockpitAction,
    WorkbenchCockpit,
    CockpitLimits,
    validate_cockpit_proposal,
)


def _seed_node(workspace_id: str) -> Node:
    return Node(
        id="n:source",
        label="Grounded source fact",
        type="entity",
        summary="A cited source fact",
        doc_id="doc:source",
        mentions=[Grounding(spans=[Span.from_dummy_for_workflow("doc:source")])],
        metadata={"workspace_id": workspace_id, "graph_space": "curated_kg"},
    )


class ScriptedCockpit:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _request, _snapshot, _observations, _progress):
        self.calls += 1
        patch = MaintenancePatch(
            patch_id="patch:cockpit:fact",
            intent=MaintenanceIntent.DERIVE_ENTITY,
            scope=MaintenanceScope(workspace_id="cockpit-test"),
            rationale="The new fact is explicitly grounded by n:source.",
            operations=[
                MaintenancePatchOperation(
                    operation_id="op:add:fact",
                    kind=MaintenanceOperationKind.ADD_NODE,
                    node_id="n:derived",
                    label="Derived grounded fact",
                    provenance=MaintenanceProvenance(
                        source_document_id="_wf:doc:source",
                        maintenance_run_id="cockpit-turn-1",
                        confidence=0.9,
                    ),
                )
            ],
        )
        return CockpitAction(
            kind="propose_patch",
            answer="I propose adding a grounded derived fact for review.",
            cited_entity_ids=["n:source"],
            patch=patch,
            rationale="The source node is in the current lens.",
        )


def test_cockpit_proposal_is_grounded_persisted_and_applied_only_after_confirmation():
    engines = build_in_memory_namespace_engines()
    try:
        workspace_id = "cockpit-test"
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            engines.kg.write.add_node(_seed_node(workspace_id))
        planner = ScriptedCockpit()
        api = WorkbenchApi(IngestPipeline(engines), cockpit_responder=planner)
        request = {
            "workspace_id": workspace_id,
            "session_id": "session-1",
            "mode": "codex",
            "query_text": "What grounded fact should we add?",
            "max_nodes": 10,
        }

        response = api.ask(request)

        assert response["agent_status"] == "cockpit_active"
        assert response["answer"]["outcome"] == "proposal"
        assert response["answer"]["proposal"]["evidence_ids"] == ["n:source"]
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            assert engines.kg.read.get_nodes(ids=["n:derived"]) == []

        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            assert engines.kg.read.get_nodes(ids=["n:derived"]) == []
        history = api.get_history(workspace_id=workspace_id, session_id="session-1")
        assert history[0]["outcome"] == "proposal"
        assert planner.calls == 1
    finally:
        engines.close()


def test_cockpit_rejects_ungrounded_patch_before_confirmation():
    engines = build_in_memory_namespace_engines()
    try:
        api = WorkbenchApi(IngestPipeline(engines))
        request = {"workspace_id": "cockpit-reject", "query_text": "nothing"}
        snapshot = api.get_lens(request)
        result = api.confirm_cockpit_proposal(
            {
                "request": request,
                "confirmed": True,
                "proposal": {
                    "operation": "maintenance_patch",
                    "lens_id": snapshot["lens_id"],
                    "source_watermark": snapshot["source_watermark"],
                    "target_ids": [],
                    "evidence_ids": [],
                    "maintenance_patch": {},
                },
            }
        )
        assert result["status"] == "rejected"
    finally:
        engines.close()


def test_durable_cockpit_interaction_persists_a_proposal_without_mutating_graph():
    engines = build_in_memory_namespace_engines()
    api = None
    try:
        workspace_id = "cockpit-test"
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            engines.kg.write.add_node(_seed_node(workspace_id))
        api = WorkbenchApi(
            IngestPipeline(engines),
            cockpit_responder=ScriptedCockpit(),
            codex_worker_count=1,
        )
        queued = api.submit_interaction(
            {"workspace_id": workspace_id, "session_id": "durable-1", "mode": "codex", "query_text": "Add a fact"}
        )
        deadline = time.monotonic() + 5
        interaction = None
        while time.monotonic() < deadline:
            interaction = api.get_interaction(workspace_id=workspace_id, interaction_id=str(queued["interaction_id"]))
            if interaction and interaction["status"] != "pending":
                break
            time.sleep(0.01)
        assert interaction is not None
        assert interaction["status"] == "completed"
        assert interaction["response"]["answer"]["outcome"] == "proposal"
        interaction_id = str(queued["interaction_id"])
        proposal = interaction["response"]["answer"]["proposal"]
        unconfirmed = api.confirm_cockpit_proposal({"workspace_id": workspace_id, "interaction_id": interaction_id})
        assert unconfirmed["status"] == "confirmation_required"
        applied = api.confirm_cockpit_proposal(
            {"workspace_id": workspace_id, "interaction_id": interaction_id, "proposal": proposal, "confirmed": True}
        )
        assert applied["status"] == "applied"
        replay = api.confirm_cockpit_proposal(
            {"workspace_id": workspace_id, "interaction_id": interaction_id, "proposal": proposal, "confirmed": True}
        )
        assert replay == applied
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            assert [node.id for node in engines.kg.read.get_nodes(ids=["n:derived"])] == ["n:derived"]
        history = api.get_history(workspace_id=workspace_id, session_id="durable-1")
        assert [item["action_kind"] for item in history].count("codex_cockpit_confirmation") == 1
    finally:
        if api is not None:
            api.close()
        engines.close()


def test_cockpit_rejects_cross_workspace_and_fabricated_grounding():
    engines = build_in_memory_namespace_engines()
    try:
        workspace_id = "cockpit-scope"
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            engines.kg.write.add_node(_seed_node(workspace_id))
        pipeline = IngestPipeline(engines)
        snapshot = pipeline.resolve_semantic_lens(SemanticLensRequest(workspace_id=workspace_id, query_text="source", max_nodes=5))
        proposal = {
            "operation": "maintenance_patch",
            "lens_id": snapshot.lens_id,
            "source_watermark": snapshot.source_watermark,
            "target_ids": [],
            "evidence_ids": ["n:source"],
            "maintenance_patch": {
                "patch_id": "p:scope",
                "intent": "derive_entity",
                "scope": {"workspace_id": "other-workspace"},
                "operations": [{
                    "operation_id": "op:scope",
                    "kind": "ADD_NODE",
                    "node_id": "n:new",
                    "provenance": {
                        "source_document_id": "_wf:doc:source",
                        "maintenance_run_id": "run-1",
                        "confidence": 0.9,
                    },
                }],
            },
        }
        assert validate_cockpit_proposal(snapshot, proposal)[1] == "maintenance_scope_workspace_mismatch"
        proposal["maintenance_patch"]["scope"]["workspace_id"] = workspace_id
        proposal["maintenance_patch"]["operations"][0]["provenance"]["source_document_id"] = "invented-doc"
        assert validate_cockpit_proposal(snapshot, proposal)[1].startswith("operation_grounding_not_in_evidence")
        proposal["evidence_ids"] = ["not-visible"]
        assert validate_cockpit_proposal(snapshot, proposal)[1] == "evidence_not_in_scoped_lens"
    finally:
        engines.close()


def test_cockpit_rejects_non_workspace_scope_and_oversized_patch():
    workspace_id = "cockpit-scope-limit"
    snapshot = SemanticLensSnapshot(
        lens_id="lens:scope-limit",
        workspace_id=workspace_id,
        source_watermark=1,
        projected_at_ms=1,
        completeness="bounded",
        nodes=(),
        edges=(),
        hyperedges=(),
        participations=(),
        anchor_explanations=(),
        selection_explanations=(),
        omitted_summary={},
        query_timing_ms=1,
    )
    conversation_patch = MaintenancePatch(
        patch_id="patch:conversation",
        intent=MaintenanceIntent.REQUEST_REVIEW,
        scope=MaintenanceScope(workspace_id=workspace_id, scope_kind="conversation", conversation_id="c:1"),
        operations=[MaintenancePatchOperation(
            operation_id="op:review",
            kind=MaintenanceOperationKind.REQUEST_REVIEW,
            reason="review",
        )],
    )
    proposal = {
        "operation": "maintenance_patch",
        "lens_id": snapshot.lens_id,
        "source_watermark": snapshot.source_watermark,
        "target_ids": [],
        "evidence_ids": [],
        "maintenance_patch": conversation_patch.model_dump(mode="json"),
    }
    assert validate_cockpit_proposal(snapshot, proposal)[1] == "cockpit_scope_not_supported"

    operations = [MaintenancePatchOperation(
        operation_id=f"op:review:{index}",
        kind=MaintenanceOperationKind.REQUEST_REVIEW,
        reason="review",
    ) for index in range(13)]
    try:
        CockpitAction(
            kind="propose_patch",
            patch=MaintenancePatch(
                patch_id="patch:oversized",
                intent=MaintenanceIntent.REQUEST_REVIEW,
                scope=MaintenanceScope(workspace_id=workspace_id),
                operations=operations,
            ),
        )
    except ValueError as exc:
        assert "at most 12 items" in str(exc)
    else:
        raise AssertionError("cockpit must reject an oversized patch")
    schema = CockpitAction.model_json_schema()
    definitions = schema.get("$defs", {})
    patch_schema = definitions.get("CockpitMaintenancePatch", {})
    assert patch_schema["properties"]["operations"]["maxItems"] == 12


def test_synchronous_cockpit_reuses_a_completed_interaction_without_rerunning():
    engines = build_in_memory_namespace_engines()
    try:
        workspace_id = "cockpit-test"
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            engines.kg.write.add_node(_seed_node(workspace_id))
        planner = ScriptedCockpit()
        api = WorkbenchApi(IngestPipeline(engines), cockpit_responder=planner)
        request = {
            "workspace_id": workspace_id,
            "session_id": "sync-1",
            "mode": "codex",
            "interaction_id": "sync-turn-1",
            "query_text": "Add a grounded fact",
        }
        first = api.ask(request)
        second = api.ask(request)
        assert first == second
        assert planner.calls == 1
    finally:
        engines.close()


def test_cockpit_rejects_follow_up_anchors_outside_current_lens():
    calls = []

    def resolve(request):
        calls.append(request)
        return SemanticLensSnapshot(
            lens_id="lens-1",
            workspace_id="scope",
            source_watermark=None,
            projected_at_ms=1,
            completeness="bounded",
            nodes=(),
            edges=(),
            hyperedges=(),
            participations=(),
            anchor_explanations=(),
            selection_explanations=(),
            omitted_summary={},
            query_timing_ms=1,
        )

    actions = iter([
        CockpitAction(kind="resolve_lens", query_text="hidden", entity_ids=["hidden"]),
        CockpitAction(kind="no_change"),
    ])
    cockpit = WorkbenchCockpit(
        resolve_lens=resolve,
        query_history=lambda *_args: [],
        limits=CockpitLimits(max_actions=2),
    )
    result = cockpit.run(
        request=SemanticLensRequest(workspace_id="scope", query_text="start"),
        session_id="s",
        responder=lambda *_args: next(actions),
        progress=lambda: None,
    )
    assert len(calls) == 1
    assert result.trace[0]["rejected_anchor_ids"] == ["hidden"]


def test_patch_application_rejects_targets_without_safe_revision():
    engines = build_in_memory_namespace_engines()
    try:
        workspace_id = "cockpit-revision"
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            engines.kg.write.add_node(_seed_node(workspace_id))
        patch = MaintenancePatch(
            patch_id="patch:revision",
            intent=MaintenanceIntent.CORRECT_FACT,
            scope=MaintenanceScope(workspace_id=workspace_id),
            operations=[
                MaintenancePatchOperation(
                    operation_id="op:tombstone",
                    kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                    target_id="n:source",
                    reason="stale fact",
                    provenance=MaintenanceProvenance(
                        source_document_id="_wf:doc:source",
                        maintenance_run_id="run-revision",
                        confidence=0.9,
                    ),
                )
            ],
        )
        result = apply_maintenance_patch_for_scope(
            engines,
            patch,
            expected_revisions={"n:source": None},
        )
        assert result.status.value == "rejected"
        assert any(issue.code == "missing_entity_revision" for issue in result.validation.issues)
    finally:
        engines.close()
