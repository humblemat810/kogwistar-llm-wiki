from __future__ import annotations

from dataclasses import dataclass

import pytest

from kogwistar.logical_refs import LogicalRef
from kogwistar_llm_wiki.multimodal_grounding import (
    EvidenceClosureValidator,
    EvidencePack,
    EvidencePackReference,
    GroundingValidationError,
    HigherOrderGrounding,
    PinnedEntityRef,
    ResolvedEntityGrounding,
    SourceEvidenceRef,
)


def _source(unit_id: str = "unit-1", *, workspace: str = "workspace-1") -> SourceEvidenceRef:
    return SourceEvidenceRef(
        workspace_id=workspace,
        source_id="source-1",
        source_revision_id="revision-1",
        source_unit_id=unit_id,
        locator_kind="text_span",
        locator_digest=f"digest-{unit_id}",
    )


def _ref(kind: str, entity_id: str, *, namespace: str = "knowledge") -> LogicalRef:
    return LogicalRef(target_namespace=namespace, target_kind=kind, target_id=entity_id)


@dataclass
class _Resolver:
    packs: dict[tuple[str, str, str], EvidencePack]
    entities: dict[tuple[str, str, str, str], ResolvedEntityGrounding]
    sources: set[tuple[str, str, str]]

    def resolve_pack(self, logical_ref: LogicalRef) -> EvidencePack | None:
        return self.packs.get(
            (logical_ref.target_namespace, logical_ref.target_kind, logical_ref.target_id)
        )

    def resolve_entity(self, logical_ref: LogicalRef, *, revision_id: str) -> ResolvedEntityGrounding | None:
        return self.entities.get(
            (logical_ref.target_namespace, logical_ref.target_kind, logical_ref.target_id, revision_id)
        )

    def resolve_source(self, source_ref: SourceEvidenceRef) -> bool:
        return (
            source_ref.workspace_id,
            source_ref.source_revision_id,
            source_ref.source_unit_id,
        ) in self.sources


def _pack_ref(pack: EvidencePack) -> EvidencePackReference:
    return EvidencePackReference(
        pack_ref=_ref("node", pack.pack_id, namespace=pack.namespace),
        expected_hash=pack.content_hash,
        source_watermark={pack.namespace: 4},
    )


def test_high_order_grounding_accepts_nodes_edges_and_direct_source_evidence() -> None:
    source = _source()
    claim_ref = _ref("node", "claim-1")
    hyperedge_ref = _ref("edge", "hyperedge-1")
    claim = PinnedEntityRef(claim_ref, revision_id="claim-rev-1", event_seq=2, role="claim")
    hyperedge = PinnedEntityRef(hyperedge_ref, revision_id="edge-rev-1", event_seq=3, role="measurement")
    pack = EvidencePack(
        pack_id="pack-1",
        namespace="knowledge",
        node_refs=(claim,),
        edge_refs=(hyperedge,),
        source_refs=(source,),
    )
    resolver = _Resolver(
        packs={("knowledge", "node", "pack-1"): pack},
        entities={
            ("knowledge", "node", "claim-1", "claim-rev-1"): ResolvedEntityGrounding(),
            ("knowledge", "edge", "hyperedge-1", "edge-rev-1"): ResolvedEntityGrounding(),
        },
        sources={("workspace-1", "revision-1", "unit-1")},
    )
    result = EvidenceClosureValidator(resolver).validate(
        HigherOrderGrounding((_pack_ref(pack),)),
        workspace_id="workspace-1",
        root_ref=_ref("edge", "tradeoff-1"),
        current_watermarks={"knowledge": 4},
    )
    assert len(result) == 64
    assert pack.node_ids == ("claim-1",)
    assert pack.edge_ids == ("hyperedge-1",)
    assert EvidencePack.from_payload(pack.to_payload()).content_hash == pack.content_hash


def test_same_pack_can_be_reused_but_recursive_pack_reference_is_rejected() -> None:
    source = _source()
    pack = EvidencePack(pack_id="pack-1", namespace="knowledge", source_refs=(source,))
    resolver = _Resolver(
        packs={("knowledge", "node", "pack-1"): pack},
        entities={},
        sources={("workspace-1", "revision-1", "unit-1")},
    )
    grounding = HigherOrderGrounding((_pack_ref(pack), _pack_ref(pack)))
    assert EvidenceClosureValidator(resolver).validate(
        grounding,
        workspace_id="workspace-1",
        root_ref=_ref("node", "summary-1"),
        current_watermarks={"knowledge": 4},
    )

    recursive = EvidencePack(
        pack_id="recursive",
        namespace="knowledge",
        node_refs=(
            PinnedEntityRef(_ref("node", "claim-1"), revision_id="r1", event_seq=1),
        ),
    )
    nested = EvidencePack(
        pack_id="nested",
        namespace="knowledge",
        node_refs=(
            PinnedEntityRef(_ref("node", "claim-1"), revision_id="r1", event_seq=1),
        ),
    )
    resolver.packs[("knowledge", "node", "recursive")] = recursive
    resolver.packs[("knowledge", "node", "nested")] = nested
    resolver.entities[("knowledge", "node", "claim-1", "r1")] = ResolvedEntityGrounding(
        evidence_pack_refs=(_pack_ref(recursive),)
    )
    with pytest.raises(GroundingValidationError, match="cyclic"):
        EvidenceClosureValidator(resolver).validate(
            HigherOrderGrounding((_pack_ref(recursive),)),
            workspace_id="workspace-1",
            root_ref=_ref("node", "summary-1"),
            current_watermarks={"knowledge": 4},
        )


def test_validator_rejects_hash_future_cross_workspace_and_unresolved_source() -> None:
    pack = EvidencePack(pack_id="pack-1", namespace="knowledge", source_refs=(_source(),))
    resolver = _Resolver(
        packs={("knowledge", "node", "pack-1"): pack}, entities={}, sources=set()
    )
    validator = EvidenceClosureValidator(resolver)
    with pytest.raises(GroundingValidationError, match="hash mismatch"):
        validator.validate(
            HigherOrderGrounding(
                (
                    EvidencePackReference(
                        pack_ref=_ref("node", "pack-1"), expected_hash="wrong"
                    ),
                )
            ),
            workspace_id="workspace-1",
            root_ref=_ref("node", "summary-1"),
        )
    with pytest.raises(GroundingValidationError, match="future"):
        validator.validate(
            HigherOrderGrounding(
                (
                    EvidencePackReference(
                        pack_ref=_ref("node", "pack-1"),
                        expected_hash=pack.content_hash,
                        source_watermark={"knowledge": 9},
                    ),
                )
            ),
            workspace_id="workspace-1",
            root_ref=_ref("node", "summary-1"),
            current_watermarks={"knowledge": 8},
        )
    with pytest.raises(GroundingValidationError, match="unresolvable"):
        validator.validate(
            HigherOrderGrounding((_pack_ref(pack),)),
            workspace_id="workspace-1",
            root_ref=_ref("node", "summary-1"),
        )


def test_validator_rejects_namespace_and_self_reference() -> None:
    source = _source()
    pack = EvidencePack(pack_id="pack-1", namespace="knowledge", source_refs=(source,))
    resolver = _Resolver(
        packs={("knowledge", "node", "pack-1"): pack}, entities={}, sources={("workspace-1", "revision-1", "unit-1")}
    )
    with pytest.raises(GroundingValidationError, match="self"):
        EvidenceClosureValidator(resolver).validate(
            HigherOrderGrounding((_pack_ref(pack),)),
            workspace_id="workspace-1",
            root_ref=_ref("node", "pack-1"),
        )
    with pytest.raises(GroundingValidationError, match="namespace"):
        EvidenceClosureValidator(resolver).validate(
            HigherOrderGrounding((_pack_ref(pack),)),
            workspace_id="workspace-1",
            root_ref=_ref("node", "summary-1", namespace="conversation"),
        )


def test_grounding_payload_rejects_malformed_reference_collections() -> None:
    with pytest.raises(ValueError, match="entries must be mappings"):
        EvidencePack.from_payload(
            {"pack_id": "pack-1", "namespace": "knowledge", "source_refs": ["not-a-ref"]}
        )
    with pytest.raises(ValueError, match="requires evidence_pack_refs"):
        HigherOrderGrounding.from_payload({})
    with pytest.raises(ValueError, match="entries must be mappings"):
        HigherOrderGrounding.from_payload({"evidence_pack_refs": ["not-a-ref"]})
