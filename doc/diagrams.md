# LLM-Wiki — Diagrams

---

## CLI Spider Map

> Read from the centre outward. Each arm is a path through the CLI.

```mermaid
mindmap
  root((llm-wiki))
    daemon
      projection
        --workspace id
        --vault /path/to/vault
        --interval 5.0
      maintenance
        --workspace id
        --interval 10.0
    --data-dir /path
    --help
```

---

## CLI Decision Snowflake

> "What do I want to do?" — pick a branch.

```mermaid
flowchart TD
    ME(["What do I want to do?"])

    ME -->|sync vault to Obsidian| PROJ["llm-wiki daemon projection\n--workspace &lt;id&gt;\n--vault &lt;path&gt;"]
    ME -->|run distillation + wisdom| MAINT["llm-wiki daemon maintenance\n--workspace &lt;id&gt;"]
    ME -->|ingest a document| INGEST["python -c\n'IngestPipeline(ws).run(doc)'"]
    ME -->|inspect projection state| SNAP["ProjectionManager\n.build_projection_snapshot(ws)"]
    ME -->|run tests| TEST["pytest tests/unit/\npytest -m integration"]

    PROJ --> POLL1["↺ polls every --interval s\nCtrl-C to stop"]
    MAINT --> POLL2["↺ polls every --interval s\nCtrl-C to stop"]
    INGEST --> PROM["pipeline.promote(entity_id)\nor promotion_mode='sync'"]
    PROM --> NOTE["→ triggers projection_request\n→ picked up by daemon projection"]
```

---

## Full Pipeline — End to End

```mermaid
flowchart LR
    DOC["📄 Document\n(markdown, PDF, text)"]

    subgraph Ingestion
        PARSE["kg-doc-parser\nextract entities + links"]
        CONV["conversation engine\nconv:fg  ·  conv:bg"]
        CAND["candidate link node\n(artifact_kind: promotion_candidate)"]
    end

    subgraph Promotion
        REVIEW{"confidence\n≥ threshold?"}
        KG["knowledge graph\nkg engine\n(artifact_kind: promoted_knowledge)"]
        PREQ["projection_request node\n(seq N, conv:bg)"]
    end

    subgraph Background Workers
        direction TB
        MW["MaintenanceDaemon\nprocess_pending_jobs()"]
        PW["ProjectionDaemon\nprocess_pending_projections()"]
    end

    subgraph Outputs
        WISDOM["wisdom engine\nwisdom nodes +\nexecution_wisdom nodes"]
        VAULT["📁 Obsidian vault\n.md files per entity"]
    end

    DOC --> PARSE --> CONV --> CAND
    CAND --> REVIEW
    REVIEW -->|auto-accept| KG
    REVIEW -->|pending| KG
    KG --> PREQ
    KG --> MW
    PREQ --> PW
    MW -->|_step_distill| WISDOM
    MW -->|_step_distill_from_history| WISDOM
    PW -->|sync_obsidian_vault| VAULT
```

---

## Maintenance Worker — Distillation Algorithm

```mermaid
flowchart TD
    START([process_pending_jobs])
    FIND["scan conv:bg for\nmaintenance_job_request nodes"]
    TRACE{"workflow_completed\ntrace exists?"}
    SKIP["skip — already done"]
    RUN["_handle_request\nrun maintenance.distillation.v1"]

    subgraph Workflow Steps
        direction TB
        D1["_step_distill\n① fetch promoted_knowledge nodes\n② group by entity label\n③ merge + deduplicate mentions\n④ tombstone existing wisdom node\n⑤ write versioned wisdom node"]
        D2["_step_distill_from_history\n① fetch workflow_step_exec failures\n② group by step_op\n③ if ≥2 signals → tombstone old\n④ write execution_wisdom node"]
        CHECK{"continue_distillation?"}
        DONE([done])
    end

    START --> FIND --> TRACE
    TRACE -->|yes| SKIP
    TRACE -->|no| RUN
    RUN --> D1 --> D2 --> CHECK
    CHECK -->|yes| D1
    CHECK -->|no| DONE
```

---

## Projection Worker — Queue Drain Algorithm

```mermaid
sequenceDiagram
    participant PW as ProjectionDaemon
    participant META as meta_sqlite<br/>(named_projections)
    participant CONV as conversation engine<br/>(conv:bg)
    participant MGR as ProjectionManager
    participant SINK as ObsidianVaultSink
    participant VAULT as 📁 Vault on disk

    loop every --interval seconds
        PW->>META: get_named_projection(ws)
        META-->>PW: last_seq = N

        PW->>CONV: get_nodes(where={seq: N+1, artifact_kind: projection_request})
        CONV-->>PW: req_node (or empty)

        alt queue empty
            PW->>PW: sleep(interval)
        else req_node found
            PW->>CONV: emit projection_status_event(seq=N+1, status=processing)
            PW->>MGR: sync_obsidian_vault(vault_root, workspace_id)
            MGR->>CONV: get KG-visible nodes
            MGR->>SINK: sink.sync(provider)
            SINK->>VAULT: write / update .md files
            SINK-->>MGR: {updated_notes, canvases}
            MGR-->>PW: ObsidianBuildResult
            PW->>CONV: emit projection_status_event(seq=N+1, status=completed)
            PW->>META: replace_named_projection(last_seq=N+1)
        end
    end
```

---

## CoW Namespace Proxy — How `_temporary_namespace` works

```mermaid
flowchart LR
    subgraph Before
        direction TB
        S1[read._e] --> E[engine\nnamespace='default']
        S2[write._e] --> E
        S3[indexing.engine] --> E
    end

    subgraph Inside _temporary_namespace block
        direction TB
        P["_NamespacedEngineProxy\nnamespace='ws:demo:conv_bg'\n(real engine untouched)"]
        S4[read._e] --> P
        S5[write._e] --> P
        S6[indexing.engine] --> P
        P -.delegates all else.-> E2[engine\nnamespace='default'\n(unchanged)"]
    end

    subgraph After
        direction TB
        S7[read._e] --> E3[engine\nnamespace='default']
        S8[write._e] --> E3
        S9[indexing.engine] --> E3
    end

    Before --> |"with _temporary_namespace(engine, 'ws:demo:conv_bg'):"| Inside _temporary_namespace block
    Inside _temporary_namespace block --> |"block exits (or raises)"| After
```

---

## Graph Space Map — Where Data Lives

```mermaid
flowchart TB
    subgraph conv["conversation engine"]
        FG["conv:fg\nraw messages\nparser artifacts\ncandidate links"]
        BG["conv:bg\nmaintenance_job_request\nprojection_request\nprojection_status_event\nworkflow run traces"]
    end

    subgraph wf["workflow engine"]
        WF["wf:maintenance\nWorkflowDesignArtifact\nWorkflowRunNode\nWorkflowStepExecNode"]
    end

    subgraph kg["knowledge graph engine"]
        KG["kg\npromoted_knowledge nodes\n(KG-visible entities)"]
    end

    subgraph wisdom["wisdom engine"]
        W1["ws:demo:wisdom\nwisdom nodes (KG aggregation)\nexecution_wisdom nodes (failure patterns)"]
    end

    subgraph obsidian["📁 Obsidian vault (filesystem)"]
        V["entity.md files\ncanvas files"]
    end

    FG -->|promotion| KG
    BG -->|discovered by MaintenanceWorker| WF
    WF -->|step exec records| wisdom
    KG -->|promoted_knowledge| wisdom
    BG -->|projection_request queue| obsidian
    KG -->|snapshot| obsidian
```
