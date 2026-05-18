# Happy Paths

## Ingest → Knowledge

upload → parse → ingest → maintenance → promotion → projection

Regular ingest does not imply the same immediate KG shape as the demo view.
The demo path now renders from `BASE_KG` through explicit graph-space reads so
the one-process walkthrough stays aligned with the same source/base write path
as normal ingest while still keeping the vault readable.

```mermaid
flowchart LR
    A[Upload source] --> B[kg-doc-parser parse]
    B --> C[Conversation namespace<br/>working artifacts]
    C --> D[Workflow namespace<br/>maintenance request]
    C --> E[Background conversation<br/>candidate link]
    E --> F[Review namespace<br/>promotion candidate]
    F --> G[KG namespace<br/>promoted knowledge]
    G --> H[Obsidian sink<br/>projection]
```

## Consolidation

idle → maintenance → merge candidates → review → update KG

## Contradiction

detect → create contradiction → review → resolve

## Wisdom

aggregate → detect pattern → create wisdom
