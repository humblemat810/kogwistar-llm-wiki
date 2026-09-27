"""Product binding for the email plugin's declarative ontology package."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from kogwistar.ontology import (
    OntologyClassDescriptor,
    OntologyEdgeShapeDescriptor,
    OntologyRelationDescriptor,
    OntologyValidationError,
    OntologyPackage,
    compose_ontology_packages,
    validate_edge_roles,
    validate_payload,
)


@dataclass(frozen=True, slots=True)
class EmailOntologyBinding:
    """Validated email ontology identity and descriptor allow-list."""

    package: OntologyPackage

    @classmethod
    def from_plugin(cls, plugin: object) -> EmailOntologyBinding:
        loader = getattr(plugin, "email_ontology_json", None)
        if not callable(loader):
            raise TypeError("email plugin does not expose its declarative ontology")
        package = OntologyPackage.model_validate(loader())
        if package.identity.ontology_id != "email":
            raise ValueError("email plugin ontology must have ontology_id=email")
        return cls(package)

    @property
    def identity(self) -> dict[str, str | int]:
        return {
            "ontology_id": self.package.identity.ontology_id,
            "version": self.package.identity.version,
            "content_sha256": self.package.identity.content_sha256,
            "schema_version": self.package.manifest.schema_version,
        }

    def validate_mapping(self, mapping_payload: dict[str, Any]) -> None:
        entities = mapping_payload.get("entities")
        relations = mapping_payload.get("relations")
        if not isinstance(entities, list) or not isinstance(relations, list):
            raise ValueError("email mapping entities and relations must be arrays")

        view = compose_ontology_packages((self.package,))
        package_id = self.package.identity.ontology_id
        descriptors = {descriptor.descriptor_id: descriptor for descriptor in self.package.descriptors}
        entity_classes: dict[str, str] = {}
        for entity in entities:
            if not isinstance(entity, Mapping):
                raise ValueError("email mapping entity must be an object")
            entity_id = str(entity.get("entity_id") or "").strip()
            class_id = str(entity.get("class_id") or "").strip()
            descriptor = descriptors.get(class_id)
            if not isinstance(descriptor, OntologyClassDescriptor):
                raise ValueError("email mapping contains a class outside the bound ontology")
            if not entity_id or entity_id in entity_classes:
                raise ValueError("email mapping entity IDs must be non-empty and unique")
            properties = entity.get("properties")
            if not isinstance(properties, Mapping):
                raise ValueError("email mapping entity properties must be an object")
            normalized_properties: dict[str, object] = {}
            for key, value in properties.items():
                if isinstance(value, (list, tuple)):
                    if len(value) == 0:
                        continue
                    normalized_properties[str(key)] = value[0] if len(value) == 1 else list(value)
                else:
                    normalized_properties[str(key)] = value
            try:
                validate_payload(
                    view,
                    class_id=f"{package_id}:{class_id}",
                    payload=normalized_properties,
                )
            except OntologyValidationError as exc:
                raise ValueError(str(exc)) from exc
            entity_classes[entity_id] = class_id

        for relation in relations:
            if not isinstance(relation, Mapping):
                raise ValueError("email mapping relation must be an object")
            relation_id = str(relation.get("relation_id") or "").strip()
            subject_id = str(relation.get("subject_id") or "").strip()
            targets = relation.get("target_ids")
            roles = relation.get("roles")
            descriptor = descriptors.get(relation_id)
            if not isinstance(descriptor, OntologyRelationDescriptor):
                raise ValueError("email mapping contains a relation outside the bound ontology")
            if subject_id not in entity_classes or not isinstance(targets, list) or not isinstance(roles, Mapping):
                raise ValueError("email mapping relation has invalid subject, targets, or roles")
            if entity_classes[subject_id] not in descriptor.source_class_ids:
                raise ValueError("email mapping relation subject violates ontology source classes")

            role_bindings: dict[str, list[dict[str, str]]] = {}
            role_entity_ids: set[str] = set()
            for role_id, values in roles.items():
                if not isinstance(role_id, str) or not isinstance(values, (list, tuple)):
                    raise ValueError("email mapping relation roles must map names to arrays")
                endpoints: list[dict[str, str]] = []
                for entity_id in values:
                    entity_key = str(entity_id)
                    class_id = entity_classes.get(entity_key)
                    if class_id is None:
                        raise ValueError("email mapping relation role references an unknown entity")
                    endpoints.append({"target_kind": "node", "class_id": f"{package_id}:{class_id}"})
                    role_entity_ids.add(entity_key)
                role_bindings[role_id] = endpoints

            shape = next(
                (
                    item
                    for item in self.package.descriptors
                    if isinstance(item, OntologyEdgeShapeDescriptor)
                    and item.relation_id == relation_id
                ),
                None,
            )
            if shape is None:
                raise ValueError(f"email relation has no ontology edge shape: {relation_id}")
            try:
                validate_edge_roles(
                    view,
                    shape_id=f"{package_id}:{shape.descriptor_id}",
                    roles=role_bindings,
                )
            except OntologyValidationError as exc:
                raise ValueError(str(exc)) from exc
            target_ids = {str(value) for value in targets}
            if target_ids != role_entity_ids - {subject_id}:
                raise ValueError("email relation targets must match non-subject role endpoints")
            if any(entity_classes[str(value)] not in descriptor.target_class_ids for value in targets):
                raise ValueError("email mapping relation target violates ontology target classes")


__all__ = ["EmailOntologyBinding"]
