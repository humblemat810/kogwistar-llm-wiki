# Inter-Repo Update (Maintenance Revision)

```mermaid
flowchart LR
    CORE[Core change] --> PIN[Immutable dependency pin]
    PIN --> PARSER[Parser CI]
    PIN --> APP[LLM-Wiki CI]
    APP --> RELEASE[Release only after all gates pass]
```

## Key Change

Maintenance is NOT a graph kind.

## Updated Responsibilities

### Kogwistar
- maintenance system primitives
- workflow execution
- event sourcing

### LLM-Wiki
- maintenance policy
- artifact mapping
- lane semantics

### Parser
- extraction only

### Sink
- projection only

## Result

Cleaner separation:
- system vs policy
