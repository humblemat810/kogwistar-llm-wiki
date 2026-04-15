# Status

## Completed

- [x] Real upstream APIs inspected and wired in from `kogwistar`, `kg-doc-parser`, and `kogwistar-obsidian-sink`
- [x] `IngestPipeline` refactored into thin orchestration
- [x] Parser call goes through `kg-doc-parser`
- [x] Ingest path uses real `kogwistar` write/ingest APIs
- [x] Foreground/background conversation lanes share one conversation engine
- [x] `workflow` is used for maintenance artifacts only
- [x] `review` remains namespace/view semantics only, not a surfaced engine
- [x] `wisdom` namespace is surfaced
- [x] Projection reads KG-visible state only
- [x] Tests rewritten around real engine fixtures and repo boundaries
- [x] Full test suite passing locally
- [x] Smoke test parameterized to mirror parser repo style
- [x] Ollama smoke case synced to the parser repo's real model choice (`gemma4:e2b`)
- [x] Docs updated with Mermaid diagrams for storage and namespace flow
- [x] Repo hygiene improved for Windows/test discovery issues
- [x] Docs reconciled for Wisdom identity, shared engine instance, and Message Channel
- [x] Namespace and visibility contract layer formalized (namespaces.py)
- [x] Model slicing and external View Modes implemented via pydantic-extension

## Missing

### Shared prerequisites

- [x] Namespace and visibility contract layer is still implicit in the app code
  - [x] Centralize namespace helpers for conversation, workflow, review, KG, and wisdom
  - [x] Centralize visibility / lane metadata helpers
  - [x] Add tests for namespace strings, lane aliases, and surfaced-engine shape

- [x] Shared artifact envelope models are still too local to the ingest pipeline
  - [x] Define reusable artifact/result models for maintenance, review, wisdom, and projection inputs (contracts.py / models.py)
  - [x] Keep KG-visible projection payloads separate from conversation/workflow artifacts (View Modes)
  - [x] Add tests for field stability and namespace routing metadata

- [ ] Background job request/result contracts are not yet factored out
  - [ ] Define a common worker entrypoint shape
  - [ ] Define a request envelope for maintenance and wisdom jobs
  - [ ] Define a run/result envelope for completed background jobs
  - [ ] Add tests that validate request/result round-trips

- [ ] Projection visibility contract is still app-local
  - [ ] Define a single filter/helper for KG-visible state
  - [ ] Define the sink-facing projection envelope
  - [ ] Add tests that exclude conversation, workflow, and review artifacts by default

### Obsidian sink projection

- [ ] Real `kogwistar-obsidian-sink` projection wiring is not yet invoked from the app
  - [ ] Add an app-side sink adapter that maps KG-visible entities to sink inputs
  - [ ] Replace the local snapshot helper with a sink-backed projection path
  - [ ] Decide whether projection is triggered directly after promotion or via events
  - [ ] Add tests that prove only KG-visible state is projected

### Maintenance worker

- [ ] Maintenance worker / daemon loop for ongoing consolidation jobs is not implemented yet
  - [ ] Consume workflow maintenance requests and emit run records
  - [ ] Route job categories by namespace and policy
  - [ ] Decide which maintenance requests are shared with conversation lanes versus workflow lanes
  - [ ] Add tests for request creation, run creation, and routing

### Wisdom distillation

- [ ] Wisdom distillation worker that writes `wisdom` artifacts from execution history is not implemented yet
  - [ ] Define the distillation trigger from workflow and conversation execution history
  - [ ] Derive reusable lessons from execution outcomes
  - [ ] Write wisdom artifacts into the `wisdom` namespace only
  - [ ] Add tests for provenance, namespace placement, and projection policy
