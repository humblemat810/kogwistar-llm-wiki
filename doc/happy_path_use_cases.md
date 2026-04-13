# Happy Path Use Cases

## 1. Purpose

Map main product use cases directly to node and edge behavior, graph kind usage, namespace placement, and pathway explanation.

---

## 2. Happy Path 1 — Add File → Knowledge → Obsidian

### Goal

A user uploads a file and it becomes grounded knowledge visible in the wiki.

### Path

1. User uploads file in app UI
2. App computes deterministic `source_id`
3. App calls parser
4. Parser returns `ParsedDocument["backend"]`
5. App calls ingest in Kogwistar
6. Kogwistar writes source / fragment / initial extracted artifacts
7. Foreground conversation namespace receives visible ingest artifacts
8. Maintenance workflow request created
9. Background lane generates:
   - candidate links
   - entity candidates
   - topic suggestions
10. Promotion candidate created
11. User or policy engine accepts candidate
12. Knowledge entity / relation written to KG namespace
13. Obsidian sink consumes projection-visible event
14. Note is materialized or updated

### Typical node / edge effects

- source node created
- fragment nodes created
- source-native edges created
- maintenance candidate nodes created
- promotion candidate node created
- accepted KG node / edge created
- projection event emitted

---

## 3. Happy Path 2 — Background Consolidation

### Goal

The system improves structure while user is idle.

### Path

1. Idle policy triggers maintenance workflow
2. Workflow scans selected knowledge and conversation artifacts
3. Background lane creates:
   - merge candidates
   - contradiction candidates
   - synthesis drafts
4. Low-risk changes may be applied automatically if policy allows
5. Higher-risk items are routed to review queue
6. Accepted results update KG
7. Projection refresh request generated if visible KG state changed

### Typical node / edge effects

- maintenance run node created
- candidate artifacts created in background lane
- accepted outcomes written to KG
- supersession edges may link old and new artifacts

---

## 4. Happy Path 3 — Contradiction Detection and Resolution

### Goal

Detect conflicting claims and resolve them without losing provenance.

### Path

1. New evidence or relation enters system
2. Contradiction scan runs
3. Background lane creates contradiction artifact
4. Review item surfaced to foreground or review namespace
5. User or policy engine decides:
   - retain both with contradiction marker
   - supersede one synthesis
   - defer decision
6. Durable contradiction marker or revised synthesis may enter KG

### Typical node / edge effects

- contradiction candidate node created
- review item created
- contradiction marker edge / node added to KG if accepted
- supersedes relation added if a synthesis is revised

---

## 5. Happy Path 4 — Wisdom Extraction

### Goal

Turn repeated maintenance or workflow outcomes into reusable lessons.

### Path

1. Multiple maintenance runs complete
2. App detects recurring success/failure pattern
3. Wisdom candidate created
4. Candidate reviewed or auto-distilled per policy
5. Wisdom artifact written to wisdom namespace
6. Future workflows consume wisdom view

### Typical node / edge effects

- wisdom candidate node created
- wisdom artifact node created
- `derived_from_execution` relations added to runs or evidence

---

## 6. Happy Path 5 — Cross-Lane Communication

### Goal

Foreground asks background to do work, background returns something reviewable.

### Path

1. User requests deeper analysis
2. App writes `maintenance_request` artifact into background inbox namespace
3. Background worker consumes it
4. Background lane creates candidate result
5. App writes `review_item` into foreground inbox or review namespace
6. User reviews and decision flows through normal promotion path

### Typical node / edge effects

- request node created in receiver namespace
- candidate node created in background lane
- review node created in foreground / review namespace
- accepted outcome promoted to KG

---

## 7. Outcome

These happy paths show how the architecture should actually be used:

- use case drives artifact type
- artifact type drives graph kind and namespace
- policy decides promotion and projection
- Kogwistar preserves lineage and authority throughout
