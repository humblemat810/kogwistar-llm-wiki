# Status

## Completed

- [x] Background job request/result envelopes defined in `models.py`
- [x] Projection visibility contract centralized in `namespaces.py`
- [x] Projection snapshot model and logic refined
- [x] Maintenance worker / daemon orchestrator (graph-native) implemented
  - [x] Consume workflow maintenance requests and emit run records
  - [x] Route job categories by namespace and policy
  - [x] Align maintenance with `conv_bg` and `workflow` engine partitioning
  - [x] Looping distillation design (`maintenance.distillation.v1`) implemented
  - [x] Verified with behavioral pinning tests

### Obsidian sink projection

- [ ] Real `kogwistar-obsidian-sink` projection wiring is not yet invoked from the app
  - [ ] Add an app-side sink adapter that maps KG-visible entities to sink inputs
  - [ ] Replace the local snapshot helper with a sink-backed projection path
  - [ ] Decide whether projection is triggered directly after promotion or via events
  - [ ] Add tests that prove only KG-visible state is projected


### Wisdom distillation

- [ ] Wisdom distillation worker that writes `wisdom` artifacts from execution history is not implemented yet
  - [ ] Define the distillation trigger from workflow and conversation execution history
  - [ ] Derive reusable lessons from execution outcomes
  - [ ] Write wisdom artifacts into the `wisdom` namespace only
  - [ ] Add tests for provenance, namespace placement, and projection policy
