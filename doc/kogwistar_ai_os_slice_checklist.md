# Kogwistar AI OS Slice Checklist

Status: Complete
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

## Core dependency gaps

- [x] engine-level lane-message repair / rebuild API
  - `engine.repair_lane_message_projection(...)` restores projected lane-message rows from graph truth
  - app-level regression proves a worker can recover after projected lane-message rows are missing
- [x] run-registry / SSE lane lifecycle surfacing
  - runtime-sent lane messages append `worker.requested` events to the core run registry
  - existing run event polling and SSE endpoints surface the lane lifecycle event
  - lane progress reports projected lane-message rows, including projection status, for app maintenance visibility
- [x] runtime-facing durable `StepContext.send_lane_message(...)`
  - `WorkflowRuntime` injects durable lane-message sender into real resolver contexts
  - `AsyncWorkflowRuntime` mirrors the same sender/sink options through its sync runtime
  - app-level regression proves a workflow resolver can create projected lane messages through the conversation engine

## App-side policy still owned by `llm-wiki`

- [x] maintenance kind selection
- [x] derived-knowledge policy
- [x] execution-wisdom policy
- [x] request / reply payload shape for the wiki workflow
