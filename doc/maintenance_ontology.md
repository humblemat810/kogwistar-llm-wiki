# Maintenance Ontology

Maintenance is a semantic layer.

Mapping:

- workflow → jobs
- conversation → critique
- knowledge → accepted results
- wisdom → distilled insight

Rule:
Maintenance artifacts evolve across graph kinds.

```mermaid
flowchart LR
    REQUEST[User or system request] --> WORKFLOW[Workflow graph\njob and run state]
    WORKFLOW --> REVIEW[Conversation graph\ncritique and candidates]
    REVIEW --> DECISION[Review or policy decision]
    DECISION --> KNOWLEDGE[Knowledge graph\naccepted durable result]
    KNOWLEDGE --> WISDOM[Wisdom graph\nreusable distilled lesson]
    KNOWLEDGE --> PROJECTION[Rebuildable projections]
    PROJECTION -. never becomes .-> TRUTH[Canonical graph truth]
```

The arrows describe artifact promotion, not graph mutation shortcuts. Each
transition remains subject to provenance, workspace scope, namespace ACLs, and
the existing proposal and acceptance fences.
