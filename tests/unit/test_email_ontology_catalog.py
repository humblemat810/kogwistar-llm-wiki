from __future__ import annotations

import sys
from pathlib import Path

import pytest
from kogwistar.engine_core.embedding_profile import EmbeddingProfile
from kogwistar.engine_core.in_memory_meta import InMemoryMetaStore
from kogwistar.ontology import OntologyPackage

from kogwistar_llm_wiki.email import (
    EmailOntologyCatalog,
    EmailOntologySemanticProjection,
)
from kogwistar_llm_wiki.workbench.workbench_api import WorkbenchApi

EMAIL_PLUGIN_SRC = Path(__file__).parents[2] / "kogwistar-email-plugin" / "src"
if str(EMAIL_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(EMAIL_PLUGIN_SRC))


def _email_package() -> OntologyPackage:
    plugin = pytest.importorskip("kogwistar_email_plugin")

    return OntologyPackage.model_validate(plugin.email_ontology_json())


def test_email_ontology_catalog_composes_and_searches_descriptors() -> None:
    catalog = EmailOntologyCatalog(
        workspace_id="workspace-a",
        packages=(_email_package(),),
    )

    results = catalog.search("mail message", principal="alice", mode="bm25")

    assert results
    exact = next(result for result in results if result.descriptor_id == "EmailMessage")
    assert exact.ontology_id == "email"
    assert exact.match == "exact"
    assert catalog.view.composition_sha256


def test_email_ontology_acl_runs_before_semantic_ranking() -> None:
    ranked_candidates: list[str] = []

    def ranker(query: str, candidates: tuple[object, ...]) -> dict[str, float]:
        del query
        ranked_candidates.extend(
            str(candidate.provider_local_id) for candidate in candidates
        )
        return {
            str(candidate.logical_id): 1.0
            for candidate in candidates
        }

    catalog = EmailOntologyCatalog(
        workspace_id="workspace-a",
        packages=(_email_package(),),
        authorize=lambda _workspace, _principal, entry: entry.provider_local_id
        == "EmailMessage",
        semantic_ranker=ranker,
    )

    results = catalog.search("email", principal="alice", mode="semantic")

    assert [result.descriptor_id for result in results] == ["EmailMessage"]
    assert ranked_candidates == ["EmailMessage"]


def test_email_ontology_semantic_projection_is_durable_and_profile_scoped() -> None:
    metadata = InMemoryMetaStore()

    def embed(text: str) -> tuple[float, float]:
        return (1.0, 0.0) if "attachment" in text.lower() else (0.0, 1.0)

    first_profile = EmbeddingProfile(
        provider="test",
        model="ontology-a",
        dimension=2,
        similarity_metric="cosine",
    )
    second_profile = EmbeddingProfile(
        provider="test",
        model="ontology-b",
        dimension=2,
        similarity_metric="cosine",
    )
    first_projection = EmailOntologySemanticProjection(
        metadata=metadata,
        workspace_id="workspace-a",
        profile=first_profile,
        embedder=embed,
    )
    second_projection = EmailOntologySemanticProjection(
        metadata=metadata,
        workspace_id="workspace-a",
        profile=second_profile,
        embedder=embed,
    )

    first_catalog = EmailOntologyCatalog(
        workspace_id="workspace-a",
        packages=(_email_package(),),
        semantic_projection=first_projection,
    )
    first_results = first_catalog.search("attachment", mode="semantic", limit=3)

    assert first_results
    assert first_results[0].descriptor_id == "Attachment"
    assert first_catalog.semantic_status["status"] == "ready"
    assert first_projection.namespace != second_projection.namespace
    assert metadata.list_named_projections(first_projection.namespace)
    assert metadata.list_named_projections(second_projection.namespace) == []

    second_catalog = EmailOntologyCatalog(
        workspace_id="workspace-a",
        packages=(_email_package(),),
        semantic_projection=second_projection,
    )
    assert second_catalog.search("attachment", mode="semantic", limit=1)[0].descriptor_id == "Attachment"
    assert metadata.list_named_projections(second_projection.namespace)


def test_email_ontology_semantic_projection_rejects_wrong_dimension() -> None:
    profile = EmbeddingProfile(
        provider="test",
        model="ontology",
        dimension=2,
    )
    projection = EmailOntologySemanticProjection(
        metadata=InMemoryMetaStore(),
        workspace_id="workspace-a",
        profile=profile,
        embedder=lambda _text: (1.0,),
    )

    with pytest.raises(ValueError, match="embedding dimension mismatch"):
        EmailOntologyCatalog(
            workspace_id="workspace-a",
            packages=(_email_package(),),
            semantic_projection=projection,
        )


def test_workbench_api_exposes_catalog_composition_and_results(pipeline) -> None:
    api = WorkbenchApi(
        pipeline,
        email_ontology_catalog_factory=lambda workspace_id: EmailOntologyCatalog(
            workspace_id=workspace_id,
            packages=(_email_package(),),
        ),
    )

    response = api.search_email_ontology(
        workspace_id="workspace-a",
        query="attachment",
        principal="alice",
    )

    assert response["status"] == "ok"
    assert response["package_identities"]
    assert any(
        item["descriptor_id"] == "Attachment"
        for item in response["results"]
    )
