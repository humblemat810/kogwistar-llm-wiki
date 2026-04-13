# Repo Boundary & Contract Catalog

## 1. Purpose

This document defines the explicit boundaries, dependency rules, and contract surfaces between:

- `kogwistar`
- `kg-doc-parser`
- `kogwistar-obsidian-sink`
- `kogwistar-llm-wiki`

It is aligned with:

- provenance-first Kogwistar entities
- event-sourced authority
- maintenance as app semantic domain
- `Model[...]` projected views for consumers

---

## 2. Canonical Principle

The system is:

- event-sourced
- provenance-first
- hypergraph-capable
- consumer-view aware

Canonical truth lives in Kogwistar. Consumer-facing contracts should prefer `Model[...]` views over ad hoc DTO duplication where possible.

---

## 3. Repo Roles

### 3.1 `kogwistar`

Owns:

- canonical graph entities
- provenance model
- append-only events
- workflow runtime
- namespace-aware replay and repair
- maintenance system capability (generic)
- CDC / ChangeBus

Does not own:

- app-specific maintenance policy
- lane semantics
- review thresholds
- promotion rules
- Obsidian projection policy

### 3.2 `kg-doc-parser`

Owns:

- source loading
- OCR / parsing
- grounded extraction
- source-native link extraction
- parse repair

Does not own:

- graph truth
- promotion policy
- maintenance policy
- projection logic

### 3.3 `kogwistar-obsidian-sink`

Owns:

- graph-to-vault projection
- markdown rendering
- path mapping
- rebuild and drift handling

Does not own:

- graph truth
- maintenance policy
- ingest logic

### 3.4 `kogwistar-llm-wiki`

Owns:

- product behavior
- maintenance policy
- lane / namespace conventions
- artifact mapping
- review logic
- promotion policy
- UI / app orchestration

---

## 4. Dependency Shape

- `kogwistar` ← bottom
- `kg-doc-parser` depends on `kogwistar`
- `kogwistar-obsidian-sink` depends on `kogwistar`
- `kogwistar-llm-wiki` composes all others

---

## 5. Contract Layers

### Layer A — canonical substrate

Owned by `kogwistar`

### Layer B — projected model views

Examples:
- `Node["backend"]`
- `Node["llm"]`
- `Node["sink"]`
- `Node["review"]`

### Layer C — integration contracts

- parsed document payload
- workflow invocation payload
- maintenance request/result
- projection request/result

### Layer D — materialized projections

- Obsidian vault
- UI representations
- review queue views

---

## 6. Boundary Rule

Use this rule:

- reusable mechanism → lower repo
- app behavior / semantics → `kogwistar-llm-wiki`

This especially applies to maintenance:

- maintenance system capability → `kogwistar`
- maintenance policy → `kogwistar-llm-wiki`

---

## 7. Outcome

These repo boundaries keep the core reusable while allowing the app to define how real use cases map to node and edge changes.
