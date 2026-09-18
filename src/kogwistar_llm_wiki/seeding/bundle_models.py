from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    def offsets_are_paired(self) -> SeedMention:
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
    def validate_graph(self) -> GraphSeedBundle:
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

    def canonicalized(self) -> GraphSeedBundle:
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
