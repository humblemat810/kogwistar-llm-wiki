"""ACL-first discovery of email ontology descriptors through Kogwistar."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import quote

from kogwistar.agent.catalog import CatalogEntry, CatalogSearchResult, CatalogStore
from kogwistar.agent.ontology import OntologyCatalogProvider
from kogwistar.engine_core.embedding_profile import (
    EmbeddingProfile,
    NamedProjectionStore,
)
from kogwistar.ontology import (
    ComposedOntologyView,
    OntologyPackage,
    compose_ontology_packages,
)

EmailCatalogAuthorizer = Callable[[str, str, CatalogEntry], bool]
EmailCatalogSemanticRanker = Callable[
    [str, tuple[CatalogEntry, ...]], dict[str, float]
]
EmailOntologyTextEmbedder = Callable[[str], Sequence[float]]

_SEMANTIC_PROJECTION_SCHEMA_VERSION = 1
_SEMANTIC_PROJECTION_NAMESPACE = "email_ontology_semantic"


def _embedding_text(entry: CatalogEntry) -> str:
    """Build the bounded, non-sensitive text represented by a descriptor vector."""

    return " ".join(
        value
        for value in (entry.name, entry.summary, *entry.aliases)
        if str(value).strip()
    ).strip()


def _validated_vector(value: Sequence[float], *, dimension: int) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("embedding output must be a numeric sequence")
    vector = tuple(float(item) for item in value)
    if len(vector) != dimension:
        raise ValueError(
            f"embedding dimension mismatch: expected {dimension}, got {len(vector)}"
        )
    if any(not math.isfinite(item) for item in vector):
        raise ValueError("embedding output must contain only finite values")
    if not any(item != 0.0 for item in vector):
        raise ValueError("embedding output must not be the zero vector")
    return vector


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class EmailOntologySemanticProjection:
    """Durable profile-scoped semantic projection for ontology descriptors.

    This intentionally uses Kogwistar's named-projection contract instead of
    introducing a second persistence API.  The complete embedding profile is
    part of the namespace, so equal dimensions cannot accidentally share data.
    """

    def __init__(
        self,
        *,
        metadata: NamedProjectionStore,
        workspace_id: str,
        profile: EmbeddingProfile,
        embedder: EmailOntologyTextEmbedder,
    ) -> None:
        normalized_workspace = str(workspace_id).strip()
        if not normalized_workspace:
            raise ValueError("workspace_id must not be empty")
        if not callable(embedder):
            raise TypeError("embedder must be callable")
        self.metadata = metadata
        self.workspace_id = normalized_workspace
        self.profile = profile
        self.embedder = embedder
        self._last_error: str | None = None
        self.namespace = (
            f"{_SEMANTIC_PROJECTION_NAMESPACE}:v{_SEMANTIC_PROJECTION_SCHEMA_VERSION}"
            f":workspace={quote(normalized_workspace, safe='')}"
            f":profile={profile.fingerprint}"
        )

    @property
    def profile_fingerprint(self) -> str:
        return self.profile.fingerprint

    @staticmethod
    def _key(logical_id: str) -> str:
        digest = hashlib.sha256(str(logical_id).encode("utf-8")).hexdigest()
        return f"descriptor:{digest}"

    def _row_payload(
        self,
        entry: CatalogEntry,
        vector: Sequence[float],
    ) -> dict[str, object]:
        text = _embedding_text(entry)
        return {
            "schema_version": _SEMANTIC_PROJECTION_SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "profile_fingerprint": self.profile.fingerprint,
            "embedding_profile": self.profile.as_dict(),
            "logical_id": entry.logical_id,
            "source_fingerprint": entry.source_fingerprint,
            "text_fingerprint": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "vector": list(vector),
        }

    def _validate_row(self, row: Mapping[str, object], *, logical_id: str) -> tuple[float, ...]:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            raise TypeError(f"semantic projection row is malformed for {logical_id}")
        if int(payload.get("schema_version", -1)) != _SEMANTIC_PROJECTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported semantic projection schema for {logical_id}")
        if str(payload.get("workspace_id")) != self.workspace_id:
            raise ValueError(f"semantic projection workspace mismatch for {logical_id}")
        if str(payload.get("profile_fingerprint")) != self.profile.fingerprint:
            raise ValueError(f"semantic projection profile mismatch for {logical_id}")
        if str(payload.get("logical_id")) != logical_id:
            raise ValueError(f"semantic projection identity mismatch for {logical_id}")
        raw_vector = payload.get("vector")
        if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, (str, bytes)):
            raise TypeError(f"semantic projection vector is malformed for {logical_id}")
        return _validated_vector(raw_vector, dimension=self.profile.dimension)

    def upsert(self, entries: Sequence[CatalogEntry]) -> None:
        """Persist descriptor vectors with CAS-protected named projections."""

        for entry in sorted(entries, key=lambda item: item.logical_id):
            vector = _validated_vector(
                self.embedder(_embedding_text(entry)),
                dimension=self.profile.dimension,
            )
            payload = self._row_payload(entry, vector)
            key = self._key(entry.logical_id)
            current = self.metadata.get_named_projection(self.namespace, key)
            if current is not None:
                current_vector = self._validate_row(current, logical_id=entry.logical_id)
                current_payload = current.get("payload")
                if current_payload == payload:
                    continue
                inserted = self.metadata.compare_and_swap_named_projection(
                    self.namespace,
                    key,
                    payload,
                    expected_last_authoritative_seq=int(
                        current.get("last_authoritative_seq", 0)
                    ),
                    expected_last_materialized_seq=int(
                        current.get("last_materialized_seq", 0)
                    ),
                    last_authoritative_seq=int(current.get("last_authoritative_seq", 0)) + 1,
                    last_materialized_seq=int(current.get("last_materialized_seq", 0)) + 1,
                    projection_schema_version=_SEMANTIC_PROJECTION_SCHEMA_VERSION,
                    materialization_status="ready",
                )
                if not inserted:
                    raise RuntimeError(
                        f"semantic projection changed concurrently for {entry.logical_id}"
                    )
                del current_vector
                continue
            inserted = self.metadata.compare_and_swap_named_projection(
                self.namespace,
                key,
                payload,
                expected_last_authoritative_seq=None,
                expected_last_materialized_seq=None,
                last_authoritative_seq=0,
                last_materialized_seq=0,
                projection_schema_version=_SEMANTIC_PROJECTION_SCHEMA_VERSION,
                materialization_status="ready",
            )
            if not inserted:
                raise RuntimeError(f"semantic projection insert raced for {entry.logical_id}")

    def rank(
        self,
        query: str,
        candidates: tuple[CatalogEntry, ...],
    ) -> dict[str, float]:
        """Return scores only for rows in this exact profile-scoped projection."""

        self._last_error = None
        try:
            query_vector = _validated_vector(
                self.embedder(str(query)),
                dimension=self.profile.dimension,
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise
        query_norm = math.sqrt(_dot(query_vector, query_vector))
        scores: dict[str, float] = {}
        for entry in candidates:
            row = self.metadata.get_named_projection(self.namespace, self._key(entry.logical_id))
            if row is None:
                continue
            vector = self._validate_row(row, logical_id=entry.logical_id)
            if self.profile.similarity_metric == "cosine":
                denominator = query_norm * math.sqrt(_dot(vector, vector))
                score = _dot(query_vector, vector) / denominator
            elif self.profile.similarity_metric == "l2":
                score = 1.0 - math.sqrt(
                    sum((left - right) ** 2 for left, right in zip(query_vector, vector, strict=True))
                )
            else:
                score = _dot(query_vector, vector)
            if math.isfinite(score) and score > 0.0:
                scores[entry.logical_id] = score
        return scores


@dataclass(frozen=True, slots=True)
class EmailOntologySearchHit:
    """Stable JSON-ready descriptor result returned after ACL filtering."""

    logical_id: str
    descriptor_id: str
    kind: str
    name: str
    summary: str
    aliases: tuple[str, ...]
    ontology_id: str
    ontology_version: str
    ontology_digest: str
    score: float
    match: str

    @classmethod
    def from_result(cls, result: CatalogSearchResult) -> EmailOntologySearchHit:
        entry = result.entry
        metadata = entry.metadata
        return cls(
            logical_id=entry.logical_id,
            descriptor_id=entry.provider_local_id,
            kind=entry.kind,
            name=entry.name,
            summary=entry.summary,
            aliases=tuple(entry.aliases),
            ontology_id=str(metadata.get("ontology_id") or ""),
            ontology_version=str(metadata.get("ontology_version") or entry.version),
            ontology_digest=str(metadata.get("ontology_digest") or entry.source_fingerprint),
            score=float(result.score),
            match=result.match,
        )

    def payload(self) -> dict[str, object]:
        return {
            "logical_id": self.logical_id,
            "descriptor_id": self.descriptor_id,
            "kind": self.kind,
            "name": self.name,
            "summary": self.summary,
            "aliases": list(self.aliases),
            "ontology_id": self.ontology_id,
            "ontology_version": self.ontology_version,
            "ontology_digest": self.ontology_digest,
            "score": self.score,
            "match": self.match,
        }


class EmailOntologyCatalog:
    """One workspace-scoped serving view over one composed ontology view.

    The source of truth remains the immutable ontology packages. Kogwistar's
    catalog performs visibility filtering before invoking a semantic ranker.
    """

    def __init__(
        self,
        *,
        workspace_id: str,
        packages: Sequence[OntologyPackage],
        authorize: EmailCatalogAuthorizer | None = None,
        semantic_ranker: EmailCatalogSemanticRanker | None = None,
        semantic_projection: EmailOntologySemanticProjection | None = None,
    ) -> None:
        normalized_workspace = str(workspace_id).strip()
        if not normalized_workspace:
            raise ValueError("workspace_id must not be empty")
        if not packages:
            raise ValueError("at least one ontology package is required")
        if semantic_ranker is not None and semantic_projection is not None:
            raise ValueError("provide semantic_ranker or semantic_projection, not both")
        if semantic_projection is not None and semantic_projection.workspace_id != normalized_workspace:
            raise ValueError("semantic projection workspace does not match catalog workspace")
        self.workspace_id = normalized_workspace
        self.view: ComposedOntologyView = compose_ontology_packages(tuple(packages))
        self._authorize = authorize or (lambda _workspace, _principal, _entry: True)
        self.semantic_projection = semantic_projection
        ranker = semantic_ranker or (
            semantic_projection.rank if semantic_projection is not None else None
        )
        self.store = CatalogStore(
            acl_enabled=True,
            acl_checker=self._catalog_acl,
            semantic_ranker=ranker,
        )
        entries: list[CatalogEntry] = []
        for package in packages:
            provider = OntologyCatalogProvider(package)
            for entry in provider.catalog_entries():
                scoped_entry = entry.model_copy(
                    update={
                        "scope": self.workspace_id,
                        "semantic_ready": ranker is not None,
                    }
                )
                entries.append(scoped_entry)
                self.store.upsert(scoped_entry)
        if semantic_projection is not None:
            semantic_projection.upsert(entries)

    @property
    def semantic_status(self) -> dict[str, object]:
        """Expose semantic availability without hiding the lexical fallback."""

        if self.semantic_projection is None:
            return {
                "status": "ready" if self.store.semantic_ranker is not None else "degraded",
                "reason": None
                if self.store.semantic_ranker is not None
                else "semantic projection is not configured",
            }
        error = self.semantic_projection._last_error
        return {
            "status": "degraded" if error else "ready",
            "profile_fingerprint": self.semantic_projection.profile_fingerprint,
            "reason": error,
        }

    def _catalog_acl(self, entry: CatalogEntry, principal: str) -> bool:
        return entry.scope == self.workspace_id and bool(
            self._authorize(self.workspace_id, principal, entry)
        )

    def search(
        self,
        query: str,
        *,
        principal: str = "system",
        mode: str = "bm25",
        limit: int = 20,
    ) -> tuple[EmailOntologySearchHit, ...]:
        results = self.store.search(
            query,
            principal=principal,
            scope=self.workspace_id,
            mode=mode,
            limit=limit,
        )
        return tuple(EmailOntologySearchHit.from_result(item) for item in results)


__all__ = [
    "EmailCatalogAuthorizer",
    "EmailCatalogSemanticRanker",
    "EmailOntologyCatalog",
    "EmailOntologySearchHit",
    "EmailOntologySemanticProjection",
    "EmailOntologyTextEmbedder",
]
