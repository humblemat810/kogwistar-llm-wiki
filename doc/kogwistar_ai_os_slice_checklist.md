# Kogwistar AI OS Slice Checklist

Status: In progress
Scope: `kogwistar-llm-wiki` integration against the stable `kogwistar` OS contract

## Current integration baseline

- [x] `llm-wiki` emits lane messages through engine-level APIs
  - `send_lane_message(...)`
  - `update_lane_message_status(...)`
- [x] `llm-wiki` does not branch on concrete metastore class names
- [x] maintenance request / reply round-trip is graph-native
- [x] request message projection is visible in the worker inbox
- [x] reply message projection is visible in the foreground inbox

## Regression guardrails

- [x] request / reply flow pinned for the current maintenance path
- [x] claim / requeue / ack flow pinned through the stable engine contract
- [x] backend-agnostic parity test added for:
  - in-memory
  - SQLite / persistent local backend
  - Postgres when `KOGWISTAR_LLM_WIKI_TEST_PG_DSN` is available
- [x] persistent SQLite baseline added to prove lane-message projection survives engine reload
- [x] worker-side request-node lookup is backend-agnostic
  - normalized to a single-root filter shape that works on persistent Chroma-backed engines too

## Known core dependency gaps

- [ ] engine-level lane-message repair / rebuild API
  - `llm-wiki` should not invent this locally
  - once core publishes a stable repair / rebuild surface, add an app-level regression immediately
- [ ] run-registry / SSE lane lifecycle surfacing
- [ ] runtime-facing durable `StepContext.send_lane_message(...)`

## App-side policy still owned by `llm-wiki`

- [x] maintenance kind selection
- [x] derived-knowledge policy
- [x] execution-wisdom policy
- [x] request / reply payload shape for the wiki workflow
