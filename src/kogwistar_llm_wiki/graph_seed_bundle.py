"""Versioned, grounded graph seed bundles for reusable workbench datasets."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kogwistar.engine_core.models import Edge, Grounding, Node, Span

from .models import NamespaceEngines
from .namespaces import GraphSpace, WorkspaceNamespaces
from .utils import _temporary_namespace


class SeedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    url: str
    revision: str
    text: str = Field(min_length=1)
    text_kind: Literal["curated_paraphrase", "verbatim"] = "curated_paraphrase"


class SeedMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    excerpt: str = Field(min_length=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def offsets_are_paired(self) -> "SeedMention":
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("start_char and end_char must both be present or both be omitted")
        if self.start_char is not None and self.end_char is not None and self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class SeedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    kind: str
    summary: str
    mentions: list[SeedMention] = Field(min_length=1)
    properties: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)


class SeedRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    relation: str
    summary: str
    sources: list[str] = Field(min_length=1)
    targets: list[str] = Field(min_length=1)
    mentions: list[SeedMention] = Field(min_length=1)
    properties: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)


class SeedAcceptanceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    expected_node_ids: list[str] = Field(default_factory=list)
    expected_relation_ids: list[str] = Field(default_factory=list)


class GraphSeedBundle(BaseModel):
    """Portable graph source with deterministic IDs and source-grounded claims."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    bundle_id: str
    title: str
    description: str
    created_at: str
    sources: list[SeedSource] = Field(min_length=1)
    nodes: list[SeedNode] = Field(min_length=1)
    edges: list[SeedRelation] = Field(default_factory=list)
    hyperedges: list[SeedRelation] = Field(default_factory=list)
    acceptance_queries: list[SeedAcceptanceQuery] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "GraphSeedBundle":
        source_ids = _unique_ids("source", [source.id for source in self.sources])
        node_ids = _unique_ids("node", [node.id for node in self.nodes])
        relation_ids = _unique_ids("relation", [item.id for item in (*self.edges, *self.hyperedges)])
        collisions = (source_ids & node_ids) | (source_ids & relation_ids) | (node_ids & relation_ids)
        if collisions:
            raise ValueError(f"IDs must be globally unique: {sorted(collisions)}")

        source_by_id = {source.id: source for source in self.sources}
        for entity in (*self.nodes, *self.edges, *self.hyperedges):
            reserved = sorted(key for key in entity.properties if key.startswith("_seed_"))
            if reserved:
                raise ValueError(f"{entity.id} uses reserved seed properties {reserved}")
            for mention in entity.mentions:
                source = source_by_id.get(mention.source_id)
                if source is None:
                    raise ValueError(f"{entity.id} references unknown source {mention.source_id}")
                _resolve_mention(mention, source)

        endpoints = source_ids | node_ids
        for relation in (*self.edges, *self.hyperedges):
            if len(relation.sources) != len(set(relation.sources)) or len(relation.targets) != len(set(relation.targets)):
                raise ValueError(f"{relation.id} contains duplicate endpoints")
            missing = (set(relation.sources) | set(relation.targets)) - endpoints
            if missing:
                raise ValueError(f"{relation.id} references unknown endpoints {sorted(missing)}")
        for relation in self.edges:
            if len(relation.sources) != 1 or len(relation.targets) != 1:
                raise ValueError(f"ordinary edge {relation.id} must have exactly one source and target")
        for relation in self.hyperedges:
            if len(set(relation.sources + relation.targets)) < 3:
                raise ValueError(f"hyperedge {relation.id} must connect at least three distinct nodes")
        for query in self.acceptance_queries:
            missing_nodes = set(query.expected_node_ids) - node_ids
            missing_relations = set(query.expected_relation_ids) - relation_ids
            if missing_nodes or missing_relations:
                raise ValueError(
                    f"acceptance query {query.query!r} references missing IDs: "
                    f"nodes={sorted(missing_nodes)}, relations={sorted(missing_relations)}"
                )
        return self

    def canonicalized(self) -> "GraphSeedBundle":
        source_by_id = {source.id: source for source in self.sources}

        def canonical_mention(mention: SeedMention) -> SeedMention:
            start, end = _resolve_mention(mention, source_by_id[mention.source_id])
            return mention.model_copy(update={"start_char": start, "end_char": end})

        def canonical_node(node: SeedNode) -> SeedNode:
            return node.model_copy(update={"mentions": [canonical_mention(item) for item in node.mentions]})

        def canonical_relation(relation: SeedRelation) -> SeedRelation:
            return relation.model_copy(update={"mentions": [canonical_mention(item) for item in relation.mentions]})

        return self.model_copy(
            update={
                "sources": sorted(self.sources, key=lambda item: item.id),
                "nodes": sorted((canonical_node(item) for item in self.nodes), key=lambda item: item.id),
                "edges": sorted((canonical_relation(item) for item in self.edges), key=lambda item: item.id),
                "hyperedges": sorted((canonical_relation(item) for item in self.hyperedges), key=lambda item: item.id),
                "acceptance_queries": sorted(self.acceptance_queries, key=lambda item: item.query),
            }
        )


@dataclass(frozen=True, slots=True)
class SeedBundleResult:
    bundle_id: str
    source_nodes_added: int
    nodes_added: int
    edges_added: int
    hyperedges_added: int
    existing_entities: int

    @property
    def total_added(self) -> int:
        return self.source_nodes_added + self.nodes_added + self.edges_added + self.hyperedges_added


def load_seed_bundle(path: str | Path) -> GraphSeedBundle:
    return GraphSeedBundle.model_validate_json(Path(path).read_text(encoding="utf-8")).canonicalized()


def dump_seed_bundle(bundle: GraphSeedBundle, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(bundle.canonicalized().model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def seed_graph_bundle(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    bundle: GraphSeedBundle,
) -> SeedBundleResult:
    canonical = bundle.canonicalized()
    namespace = WorkspaceNamespaces(workspace_id).curated_kg_space
    source_by_id = {source.id: source for source in canonical.sources}
    source_added = node_added = edge_added = hyperedge_added = existing = 0

    with _temporary_namespace(engines.kg, namespace):
        current_nodes = {str(item.id): item for item in engines.kg.read.get_nodes(limit=10_000)}
        current_edges = {str(item.id): item for item in engines.kg.read.get_edges(limit=10_000)}
        for source in canonical.sources:
            if _accept_existing(current_nodes.get(source.id), canonical.bundle_id):
                existing += 1
                continue
            engines.kg.write.add_node(_source_node(workspace_id, canonical, source))
            source_added += 1
        for node in canonical.nodes:
            if _accept_existing(current_nodes.get(node.id), canonical.bundle_id):
                existing += 1
                continue
            engines.kg.write.add_node(_knowledge_node(workspace_id, canonical, node, source_by_id))
            node_added += 1
        for relation, is_hyperedge in [
            *((item, False) for item in canonical.edges),
            *((item, True) for item in canonical.hyperedges),
        ]:
            if _accept_existing(current_edges.get(relation.id), canonical.bundle_id):
                existing += 1
                continue
            engines.kg.write.add_edge(
                _relation_edge(workspace_id, canonical, relation, source_by_id, is_hyperedge=is_hyperedge)
            )
            if is_hyperedge:
                hyperedge_added += 1
            else:
                edge_added += 1

    return SeedBundleResult(
        bundle_id=canonical.bundle_id,
        source_nodes_added=source_added,
        nodes_added=node_added,
        edges_added=edge_added,
        hyperedges_added=hyperedge_added,
        existing_entities=existing,
    )


def export_graph_seed_bundle(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    bundle_id: str,
) -> GraphSeedBundle:
    """Reconstruct a portable bundle from persisted graph entities."""

    namespace = WorkspaceNamespaces(workspace_id).curated_kg_space
    with _temporary_namespace(engines.kg, namespace):
        nodes = [
            item
            for item in engines.kg.read.get_nodes(limit=10_000, resolve_mode="include_tombstones")
            if str(item.metadata.get("seed_bundle_id") or "") == bundle_id
        ]
        edges = [
            item
            for item in engines.kg.read.get_edges(limit=10_000, resolve_mode="include_tombstones")
            if _seed_owner(item) == bundle_id
        ]
    if not nodes:
        raise ValueError(f"seed bundle {bundle_id!r} is not present in workspace {workspace_id!r}")

    source_nodes = [item for item in nodes if item.metadata.get("artifact_kind") == "seed_source"]
    knowledge_nodes = [item for item in nodes if item.metadata.get("artifact_kind") == "seed_knowledge"]
    if not source_nodes:
        raise ValueError(f"seed bundle {bundle_id!r} has no persisted source entities")
    manifest = source_nodes[0].metadata
    sources = [
        SeedSource(
            id=str(item.id),
            title=item.label,
            url=str(item.metadata["seed_source_url"]),
            revision=str(item.metadata["seed_source_revision"]),
            text=str(item.metadata["seed_source_text"]),
            text_kind=str(item.metadata["seed_source_text_kind"]),
        )
        for item in source_nodes
    ]
    exported_nodes = [
        SeedNode(
            id=str(item.id),
            label=item.label,
            kind=str(item.metadata["seed_kind"]),
            summary=item.summary,
            mentions=_mentions_from_entity(item),
            properties=_user_properties(item.properties),
        )
        for item in knowledge_nodes
    ]
    exported_edges: list[SeedRelation] = []
    exported_hyperedges: list[SeedRelation] = []
    for item in edges:
        relation = SeedRelation(
            id=str(item.id),
            label=item.label,
            relation=item.relation,
            summary=item.summary,
            sources=list(item.source_ids),
            targets=list(item.target_ids),
            mentions=_mentions_from_entity(item),
            properties=_user_properties(item.properties),
        )
        relation_kind = str((item.properties or {}).get("_seed_relation_kind") or "")
        target = exported_hyperedges if relation_kind == "hyperedge" else exported_edges
        target.append(relation)
    acceptance_json = str(manifest.get("seed_acceptance_queries_json") or "[]")
    return GraphSeedBundle(
        bundle_id=bundle_id,
        title=str(manifest["seed_bundle_title"]),
        description=str(manifest["seed_bundle_description"]),
        created_at=str(manifest["seed_bundle_created_at"]),
        sources=sources,
        nodes=exported_nodes,
        edges=exported_edges,
        hyperedges=exported_hyperedges,
        acceptance_queries=[SeedAcceptanceQuery.model_validate(item) for item in json.loads(acceptance_json)],
    ).canonicalized()


def _unique_ids(kind: str, values: list[str]) -> set[str]:
    unique = set(values)
    if len(unique) != len(values):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        raise ValueError(f"duplicate {kind} IDs: {duplicates}")
    return unique


def _resolve_mention(mention: SeedMention, source: SeedSource) -> tuple[int, int]:
    if mention.start_char is not None and mention.end_char is not None:
        if source.text[mention.start_char : mention.end_char] != mention.excerpt:
            raise ValueError(f"mention excerpt does not match half-open offsets in {source.id}")
        return mention.start_char, mention.end_char
    first = source.text.find(mention.excerpt)
    if first < 0:
        raise ValueError(f"mention excerpt is absent from {source.id}: {mention.excerpt!r}")
    if source.text.find(mention.excerpt, first + 1) >= 0:
        raise ValueError(f"mention excerpt is ambiguous within {source.id}: {mention.excerpt!r}")
    return first, first + len(mention.excerpt)


def _metadata(workspace_id: str, bundle: GraphSeedBundle, **extra: str) -> dict[str, str]:
    return {
        "workspace_id": workspace_id,
        "graph_space": GraphSpace.CURATED_KG.value,
        "seed_bundle_id": bundle.bundle_id,
        "seed_schema_version": str(bundle.schema_version),
        "lifecycle_status": "active",
        **extra,
    }


def _source_node(workspace_id: str, bundle: GraphSeedBundle, source: SeedSource) -> Node:
    acceptance = json.dumps([item.model_dump(mode="json") for item in bundle.acceptance_queries], sort_keys=True)
    return Node(
        id=source.id,
        label=source.title,
        type="entity",
        summary=f"Primary source for seed bundle {bundle.title}.",
        doc_id=source.id,
        mentions=[Grounding(spans=[_span(source, 0, len(source.text), source.text)])],
        properties={"url": source.url, "revision": source.revision, "text_kind": source.text_kind},
        metadata=_metadata(
            workspace_id,
            bundle,
            artifact_kind="seed_source",
            seed_source_url=source.url,
            seed_source_revision=source.revision,
            seed_source_text=source.text,
            seed_source_text_kind=source.text_kind,
            seed_bundle_title=bundle.title,
            seed_bundle_description=bundle.description,
            seed_bundle_created_at=bundle.created_at,
            seed_acceptance_queries_json=acceptance,
        ),
    )


def _knowledge_node(
    workspace_id: str,
    bundle: GraphSeedBundle,
    node: SeedNode,
    sources: dict[str, SeedSource],
) -> Node:
    return Node(
        id=node.id,
        label=node.label,
        type="entity",
        summary=node.summary,
        doc_id=node.mentions[0].source_id,
        mentions=_groundings(node.mentions, sources),
        properties=_seed_properties(node.properties, bundle=bundle, kind="node"),
        metadata=_metadata(workspace_id, bundle, artifact_kind="seed_knowledge", seed_kind=node.kind),
    )


def _relation_edge(
    workspace_id: str,
    bundle: GraphSeedBundle,
    relation: SeedRelation,
    sources: dict[str, SeedSource],
    *,
    is_hyperedge: bool,
) -> Edge:
    return Edge(
        id=relation.id,
        label=relation.label,
        type="relationship",
        summary=relation.summary,
        doc_id=relation.mentions[0].source_id,
        source_ids=relation.sources,
        target_ids=relation.targets,
        relation=relation.relation,
        source_edge_ids=[],
        target_edge_ids=[],
        mentions=_groundings(relation.mentions, sources),
        properties=_seed_properties(
            relation.properties,
            bundle=bundle,
            kind="hyperedge" if is_hyperedge else "edge",
        ),
        metadata=_metadata(
            workspace_id,
            bundle,
            artifact_kind="seed_relation",
            seed_relation_kind="hyperedge" if is_hyperedge else "edge",
        ),
    )


def _groundings(mentions: list[SeedMention], sources: dict[str, SeedSource]) -> list[Grounding]:
    result: list[Grounding] = []
    for mention in mentions:
        source = sources[mention.source_id]
        start, end = _resolve_mention(mention, source)
        result.append(Grounding(spans=[_span(source, start, end, mention.excerpt)]))
    return result


def _span(source: SeedSource, start: int, end: int, excerpt: str) -> Span:
    return Span(
        collection_page_url=source.url,
        document_page_url=source.url,
        doc_id=source.id,
        insertion_method="curated_seed",
        page_number=1,
        start_char=start,
        end_char=end,
        excerpt=excerpt,
        context_before=source.text[max(0, start - 80) : start],
        context_after=source.text[end : end + 80],
        chunk_id=None,
        source_cluster_id=source.id,
    )


def _mentions_from_entity(entity: Node | Edge) -> list[SeedMention]:
    return [
        SeedMention(
            source_id=span.doc_id,
            excerpt=span.excerpt,
            start_char=span.start_char,
            end_char=span.end_char,
        )
        for grounding in entity.mentions
        for span in grounding.spans
    ]


def _accept_existing(entity: Node | Edge | None, bundle_id: str) -> bool:
    if entity is None:
        return False
    owner = _seed_owner(entity)
    if owner != bundle_id:
        raise ValueError(f"entity ID {entity.id!r} already belongs to bundle {owner or '<unowned>'!r}")
    return True


def _seed_owner(entity: Node | Edge) -> str:
    return str(
        entity.metadata.get("seed_bundle_id")
        or (entity.properties or {}).get("_seed_bundle_id")
        or ""
    )


def _seed_properties(
    properties: dict[str, str | int | float | bool | list[str]],
    *,
    bundle: GraphSeedBundle,
    kind: str,
) -> dict[str, str | int | float | bool | list[str]]:
    return {
        **properties,
        "_seed_bundle_id": bundle.bundle_id,
        "_seed_schema_version": bundle.schema_version,
        "_seed_relation_kind": kind,
    }


def _user_properties(properties: object) -> dict[str, str | int | float | bool | list[str]]:
    if not isinstance(properties, dict):
        return {}
    return {
        str(key): value
        for key, value in properties.items()
        if not str(key).startswith("_seed_")
    }


__all__ = [
    "GraphSeedBundle",
    "SeedAcceptanceQuery",
    "SeedBundleResult",
    "SeedMention",
    "SeedNode",
    "SeedRelation",
    "SeedSource",
    "dump_seed_bundle",
    "export_graph_seed_bundle",
    "load_seed_bundle",
    "seed_graph_bundle",
]
