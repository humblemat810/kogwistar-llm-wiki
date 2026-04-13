# Update & Delete Semantics

## 1. Purpose

Define update and delete behavior consistent with:

- append-only authoritative events
- provenance preservation
- hypergraph-capable relations
- app-level maintenance policy

---

## 2. Core Rule

Update and delete are **not plain CRUD**.

Normal behavior should preserve:

- history
- provenance
- derivation lineage
- replayability

---

## 3. Structural Update

### 3.1 Update means new state, not silent overwrite

Preferred pattern:

1. append event
2. create new or replacement artifact if needed
3. link old and new via `supersedes`, `derived_from`, or equivalent relation

### 3.2 Examples

- improved summary
- refined synthesis
- updated relation
- revised contradiction set

---

## 4. Structural Delete

### 4.1 Normal delete should not be physical removal

Preferred structural behaviors:

- tombstone / delete event
- supersession
- projection hiding
- UI hiding

### 4.2 Hard delete

Should be rare and reserved for:

- admin repair
- legal/privacy cleanup
- corruption recovery

---

## 5. Structural vs Application Lifecycle

### 5.1 Structural lifecycle

Examples:

- active
- superseded
- tombstoned

These are about graph continuity and history.

### 5.2 Application lifecycle

Examples:

- candidate
- needs_review
- accepted
- rejected
- deferred

These are about app policy and review status.

Important:

- `rejected` is **not** a core Kogwistar lifecycle primitive
- it should be modeled as application state, not structural delete

---

## 6. Required Relation Families

Useful relation families include:

- `supersedes`
- `derived_from`
- `supports`
- `contradicts`
- `invalidates`
- `maintenance_result_for`

---

## 7. Domain-Specific Guidance

### Conversation artifacts

- update via new artifact version or follow-up node
- delete via hide / supersede / tombstone when necessary

### Maintenance artifacts

- update via revised candidate / critique / decision
- delete via supersession or lifecycle state change

### Knowledge artifacts

- update via new evidence-backed version
- delete rarely; prefer deprecation, contradiction marker, or supersession

### Wisdom artifacts

- update via refined version
- avoid hard removal unless truly invalid or legally required

---

## 8. Anti-Patterns

- silent overwrite of authoritative entities
- hard delete in ordinary flows
- losing provenance on update
- mixing app review semantics into core structural semantics

---

## 9. Outcome

This model keeps Kogwistar aligned with append-only, provenance-first design while leaving app policy free to express review and acceptance states.
