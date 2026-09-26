"""Product binding for the email plugin's declarative ontology package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kogwistar.ontology import OntologyPackage


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
        descriptor_ids = {descriptor.descriptor_id for descriptor in self.package.descriptors}
        for entity in mapping_payload.get("entities", []):
            if not isinstance(entity, dict) or entity.get("class_id") not in descriptor_ids:
                raise ValueError("email mapping contains a class outside the bound ontology")
        for relation in mapping_payload.get("relations", []):
            if not isinstance(relation, dict) or relation.get("relation_id") not in descriptor_ids:
                raise ValueError("email mapping contains a relation outside the bound ontology")


__all__ = ["EmailOntologyBinding"]
