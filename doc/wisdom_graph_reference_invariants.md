# Wisdom Graph Reference Invariants

## Purpose

This note records the current intended boundary between `conversation`,
`workflow`, `knowledge`, and `wisdom` so the implementation does not smuggle
in new reference-direction rules by accident.

It is a companion note to the wisdom ARD and the maintenance ontology.

## Intended Semantics

- `conversation` is the working-memory graph.
- `workflow` is the execution graph.
- `knowledge` is the durable promoted knowledge graph.
- `wisdom` is the reusable lesson graph.

The safe baseline is:

- `conversation` may reference `wisdom` through stable pointer/citation style
  artifacts.
- `wisdom` may read from `conversation`, `workflow`, and `knowledge` artifacts
  as source material for derivation.
- `wisdom` should not become live execution state.
- `conversation` should not hold mutable variable references into `wisdom`.
- `workflow` should not be replaced by `wisdom`; it may be guided by it.

## Open Invariant

The unresolved design question is whether conversation-to-wisdom references are
limited to pointers/citations, or whether some richer read-only structural links
are allowed.

Until that is explicitly decided:

- keep wisdom helpers policy-light
- keep conversation/wisdom links one-way in the implementation
- avoid treating wisdom as mutable runtime state

## Example

Good:

- conversation turn -> pointer node -> wisdom artifact
- workflow step -> reads wisdom artifact -> chooses next route

Not yet decided:

- conversation node -> direct structural dependency edge to wisdom node
- conversation node -> live variable binding into wisdom-backed state

## Follow-Up

If the team wants richer cross-graph links later, that should be captured as a
separate ARD and promoted through the status board before implementation.
