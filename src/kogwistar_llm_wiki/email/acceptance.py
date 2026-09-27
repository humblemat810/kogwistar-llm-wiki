"""Explicit acceptance of email structural mapping proposals."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from ..maintenance.maintenance_patch_apply import (
    MaintenancePatchApplyResult,
    apply_maintenance_patch_for_scope,
)
from ..maintenance.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from ..models import NamespaceEngines
from .runtime import EmailEvidenceRecord


@dataclass(frozen=True, slots=True)
class EmailProposalPatch:
    patch: MaintenancePatch
    source_document_id: str
    mapping_id: str


class EmailProposalMaterializer:
    """Build and explicitly apply grounded email proposal patches."""

    def build_patch(
        self,
        *,
        record: EmailEvidenceRecord,
        source_document_id: str | None = None,
        confidence: float = 0.75,
    ) -> EmailProposalPatch:
        mapping = record.mapping_payload
        authoritative_source_document_id = record.source_document_id
        if not authoritative_source_document_id or not authoritative_source_document_id.strip():
            raise ValueError("email evidence lacks authoritative source_document_id")
        if source_document_id is not None and source_document_id != authoritative_source_document_id:
            raise ValueError("source_document_id does not match immutable email evidence")
        mapping_id = str(mapping.get("mapping_id") or "")
        if not mapping_id:
            raise ValueError("email mapping must have a deterministic mapping_id")
        entities = mapping.get("entities")
        relations = mapping.get("relations")
        if not isinstance(entities, list) or not isinstance(relations, list):
            raise TypeError("email mapping entities and relations must be arrays")
        namespace = f"email:{_digest(record.stream_id)}:"
        entity_ids: dict[str, str] = {}
        operations: list[MaintenancePatchOperation] = []
        run_id = f"email-mapping:{mapping_id}"

        def provenance(operation_id: str) -> MaintenanceProvenance:
            return MaintenanceProvenance(
                source_document_id=authoritative_source_document_id,
                maintenance_run_id=f"{run_id}:{operation_id}",
                confidence=confidence,
            )

        for entity in entities:
            if not isinstance(entity, Mapping):
                raise TypeError("email mapping entity must be an object")
            candidate_id = str(entity.get("entity_id") or "").strip()
            class_id = str(entity.get("class_id") or "").strip()
            if not candidate_id or not class_id:
                raise ValueError("email mapping entities require entity_id and class_id")
            graph_id = f"{namespace}{_digest(candidate_id)}"
            if candidate_id in entity_ids and entity_ids[candidate_id] != graph_id:
                raise ValueError("email entity identity is not deterministic")
            entity_ids[candidate_id] = graph_id
            properties = entity.get("properties")
            if not isinstance(properties, Mapping):
                raise TypeError("email entity properties must be an object")
            flattened = {
                "email_entity_id": candidate_id,
                "ontology_class": class_id,
                **{
                    str(key): "; ".join(str(value) for value in values)
                    if isinstance(values, (list, tuple))
                    else str(values)
                    for key, values in properties.items()
                },
            }
            operation_id = f"add-node:{_digest(candidate_id)}"
            operations.append(
                MaintenancePatchOperation(
                    operation_id=operation_id,
                    kind=MaintenanceOperationKind.ADD_NODE,
                    node_id=graph_id,
                    label=class_id,
                    node_type=class_id,
                    properties=flattened,
                    provenance=provenance(operation_id),
                    reason="Accepted email structural proposal",
                )
            )

        for index, relation in enumerate(relations):
            if not isinstance(relation, Mapping):
                raise TypeError("email mapping relation must be an object")
            relation_id = str(relation.get("relation_id") or "").strip()
            subject_id = str(relation.get("subject_id") or "").strip()
            targets = relation.get("target_ids")
            if not relation_id or subject_id not in entity_ids or not isinstance(targets, list):
                raise ValueError("email mapping relation has invalid subject or targets")
            for target_index, target in enumerate(targets):
                target_id = entity_ids.get(str(target))
                if target_id is None:
                    raise ValueError("email relation target is not declared as an entity")
                material = f"{relation_id}:{subject_id}:{target_id}:{index}:{target_index}"
                operation_id = f"add-edge:{_digest(material)}"
                operations.append(
                    MaintenancePatchOperation(
                        operation_id=operation_id,
                        kind=MaintenanceOperationKind.ADD_EDGE,
                        edge_id=f"{namespace}{_digest(material)}",
                        from_node_id=entity_ids[subject_id],
                        to_node_id=target_id,
                        relation=relation_id,
                        label=relation_id,
                        properties={"email_relation_id": relation_id},
                        provenance=provenance(operation_id),
                        reason="Accepted email structural proposal",
                    )
                )
        patch = MaintenancePatch(
            patch_id=f"email-patch:{mapping_id}",
            intent=MaintenanceIntent.DERIVE_ENTITY,
            scope=MaintenanceScope(workspace_id=record.workspace_id),
            operations=operations,
            rationale="Explicitly accepted email structural mapping",
        )
        return EmailProposalPatch(
            patch=patch,
            source_document_id=authoritative_source_document_id,
            mapping_id=mapping_id,
        )

    def accept(
        self,
        engines: NamespaceEngines,
        proposal: EmailProposalPatch,
        *,
        confirmed: bool,
    ) -> MaintenancePatchApplyResult | None:
        if not confirmed:
            return None
        return apply_maintenance_patch_for_scope(engines, proposal.patch)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


__all__ = ["EmailProposalMaterializer", "EmailProposalPatch"]
