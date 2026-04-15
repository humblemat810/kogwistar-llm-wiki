# Lane & Namespace Convention

## 1. Purpose

Define foreground and background lane behavior using namespace plus metadata.

This document keeps the ontology small:

- one conversation graph domain
- two operating lanes
- namespace-based operational separation
- metadata-based semantic filtering (e.g., inbox/notification intent)
- generalized message channel targets

---

## 2. Namespace Pattern

Suggested patterns, refer to kogwistar style first, if not, below is a possible shape:

- Foreground conversation:
  - `ws:{workspace_id}:conv:fg`
- Background maintenance conversation:
  - `ws:{workspace_id}:conv:bg`
- Maintenance workflow:
  - `ws:{workspace_id}:wf:maintenance`
- Knowledge:
  - `ws:{workspace_id}:kg`
- Wisdom:
  - `ws:{workspace_id}:wisdom`

---

## 3. Required Metadata

Recommended `Node.properties` fields:

- `conversation_lane`: `foreground` | `background`
- `origin`: `user` | `assistant` | `maintenance`
- `visibility`: `user` | `review` | `system`

Notes:

- metadata handles semantic meaning
- "Inbox" or "Notification" intent is expressed through metadata (e.g., `intent: notification`), not namespaces
- do not rely on namespace alone for user visibility or policy

---

## 4. Communication Model

### 4.1 Preferred approach

Sending a cross-lane message should normally mean:

- create a graph artifact in the receiver-owned namespace
- emit authoritative event
- let workers / UI consume by namespace and metadata

### 4.2 Generalized Message Channel

To facilitate future external integrations, message passing should be generalized into a "Message Channel" abstraction where:
- Target is specified as `foreground` or `background`.
- Shape fixing/normalization is handled by the channel helper.
- Metadata preserves the specific intent (request, notify, alert).

Foreground requests consolidation:

- create `maintenance_request` node
- namespace: `ws:{workspace_id}:chan:bg_inbox`
- link it to source artifact with `maintenance_result_for` / `derived_from` / `requests_analysis_of`

Background returns review item:

- create `review_item` node
- namespace: `ws:{workspace_id}:chan:fg_inbox` or review namespace
- link it to background candidate

### 4.3 Why this is preferred

- durable
- replayable
- inspectable
- provenance-preserving
- aligned with graph/event substrate

---

## 5. Lane Rules

### Foreground lane

Use for:

- user-visible turns
- explicit curation
- active task context
- surfaced review results

### Background lane

Use for:

- maintenance critique
- candidate generation
- consolidation reasoning
- synthesis preparation
- contradiction preparation

### Promotion rule

Background artifacts do not become KG truth automatically unless policy allows it.

---

## 6. Anti-Patterns

- using one mixed public channel for everything
- treating namespace as the only semantic marker
- writing hidden background artifacts directly into foreground lane
- forcing all maintenance artifacts into conversation when workflow or knowledge is a better fit

---

## 7. Outcome

This convention gives:

- separate sequence and replay by namespace
- clean foreground/background behavior
- no need for a new core graph kind
