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
    MW -->|derive_problem_solving_wisdom_from_history| WISDOM
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
    RUN["_handle_request\nroute by maintenance_kind"]

    subgraph Workflow Steps
        direction TB
        D1["maintenance.derived_knowledge.v1 / _step_distill\n① fetch promoted_knowledge nodes\n② group by entity label\n③ merge + deduplicate mentions\n④ write replacement derived_knowledge node\n⑤ redirect old ids to new id"]
        D2["execution_wisdom maintenance kind\n① fetch workflow_step_exec failures\n② group by step_op\n③ if ≥2 signals → write replacement execution_wisdom node\n④ redirect old ids to new id"]
        DONE([done])
    end

    START --> FIND --> TRACE
    TRACE -->|yes| SKIP
    TRACE -->|no| RUN
    RUN --> D1 --> DONE
    RUN --> D2 --> DONE
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
        W1["ws:demo:wisdom\nexecution_wisdom nodes (failure patterns)"]
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

---

## Durable Job Queue Facade

```mermaid
flowchart LR
    APP["llm-wiki workers / ingest"]
    JOBS["engine.jobs\n typed facade"]
    META["meta store\n index_jobs"]
    WORKER["MaintenanceWorker /\nProjectionWorker"]

    APP -->|enqueue job_id + namespace + payload| JOBS
    JOBS -->|preserve coalescing\nnamespace/entity/job kind| META
    WORKER -->|claim(namespace)| JOBS
    JOBS -->|lease rows| META
    WORKER -->|mark_done / retry_or_fail| JOBS
    JOBS -->|DONE / retry / FAILED| META
```

---

## Lane Message Projection Repair

```mermaid
flowchart TB
    GRAPH["graph truth\nlane_message nodes"]
    EVENTS["entity_events\nADD / REPLACE order"]
    PROJ["projected lane-message rows\nworker/foreground inbox serving table"]
    REPAIR["engine.repair_lane_message_projection(namespace)"]
    WORKER["llm-wiki worker\nclaims inbox row"]

    GRAPH --> EVENTS
    EVENTS --> REPAIR
    GRAPH --> REPAIR
    REPAIR -->|safe repair inserts missing rows| PROJ
    PROJ --> WORKER
```

---

## Promotion Convergence Happy Path

```mermaid
flowchart LR
    SRC["source_uri + workspace_id"] --> DOC["stable source_document_id"]
    DOC --> CL["candidate_link\nstable id"]
    CL --> PC["promotion_candidate\nstable id"]
    PC --> DEC{"promotion policy"}
    DEC -->|promote| PK["promoted_knowledge\nstable id"]
    DOC --> MR["maintenance request\nstable node id"]
    MR --> LM["maintenance lane request\nidempotency key"]
    PK --> PJ["projection job\ncoalesced durable job"]
```

---

## Core Lane Idempotency

```mermaid
flowchart TD
    SEND["send_lane_message(..., idempotency_key=K)"] --> SEARCH["search lane_message graph truth\nnamespace + key + common filters"]
    SEARCH --> MATCH{"existing match?"}
    MATCH -->|yes| VALIDATE["validate stable send-shape"]
    VALIDATE --> REPROJECT{"projected row missing?"}
    REPROJECT -->|yes| REPAIR["re-project serving row"]
    REPROJECT -->|no| RETURN["return existing message_id"]
    REPAIR --> RETURN
    MATCH -->|no| CREATE["create message node + semantic edges"]
    CREATE --> PROJECT["project serving row"]
    PROJECT --> RETURNNEW["return new message_id"]
```

---

## Normal Convergent Happy Path

```mermaid
sequenceDiagram
    participant Ingest as IngestPipeline
    participant Core as Kogwistar core
    participant Worker as MaintenanceWorker
    participant Proj as ProjectionWorker
    participant Vault as Obsidian vault

    Ingest->>Core: register source + stable promotion-chain ids
    Ingest->>Core: send maintenance request (idempotency key)
    Ingest->>Core: enqueue maintenance job once
    Ingest->>Core: promote knowledge once
    Ingest->>Core: enqueue projection job once
    Worker->>Core: claim maintenance request
    Worker->>Core: send foreground reply (idempotency key)
    Worker->>Core: complete request message + durable job
    Proj->>Core: process projection job
    Proj->>Core: manifest desired -> ready
    Proj->>Vault: sync materialized notes
```

---

## Runtime Lane Lifecycle To SSE

```mermaid
sequenceDiagram
    participant R as WorkflowRuntime
    participant CTX as StepContext
    participant CONV as conversation engine
    participant REG as run registry
    participant SSE as existing run events SSE

    R->>CTX: construct with lane sender + event sink
    CTX->>CONV: send_lane_message(...)
    CONV-->>CTX: durable lane message id
    CTX->>REG: append worker.requested
    REG-->>SSE: /api/runs/{run_id}/events
```

---

## Startup Recovery Coordinator

Wording note:

- Workflow is what runs. Runtime is how it runs. Service health is which
  long-running operational process is alive.
- The recovery surface inspects service health as an operator-visible latest
  state. It is not a universal actor or capability registry.

```mermaid
flowchart LR
    DAEMON["llm-wiki daemon startup"]
    REC["engine.recovery.recover_startup"]
    QUEUES["durable queues\nindex_jobs"]
    LANES["lane rows\nprojected inboxes"]
    CHECKPOINTS["workflow checkpoints"]
    RUNS["run history"]
    DEAD["dead letters"]
    SERVICE["service health\nlatest projection"]
    APP["app surfaces\nmanifest / vault"]
    REPORT["RecoveryReport\noperator visibility"]

    DAEMON --> REC
    REC --> QUEUES
    REC --> LANES
    REC --> CHECKPOINTS
    REC --> RUNS
    REC --> DEAD
    REC --> SERVICE
    REC --> APP
    REC --> REPORT
```

---

## Knowledge Policy Boundary

```mermaid
flowchart TB
    CORE["kogwistar.policy\nprotocols + conservative defaults"]
    APPPOL["llm-wiki policies\nconfigured policy instances"]
    TAX["LlmWikiArtifactTaxonomy\napp artifact names"]
    CALLS["ingest / maintenance / projection call sites"]

    CORE -->|generic decisions| APPPOL
    TAX -->|app vocabulary| APPPOL
    APPPOL --> CALLS
    CORE -.does not classify app vocabulary.-> TAX
```

---

## Service Health Registry

```mermaid
flowchart TB
    SVC["long-running service\nmaintenance/projection daemon"]
    REG["engine.service_health\nServiceHealthRegistry"]
    GRAPH["graph/oplog sparse lifecycle facts\nregistered, started, stopped,\nstale, recovered, config changed"]
    PROJ["durable latest health projection\nservice_id, instance_id,\nlast_seen_ms, status, last_error"]
    REC["engine.recovery.inspect"]

    SVC -->|declare / start / stop| REG
    REG -->|meaningful transitions only| GRAPH
    SVC -->|heartbeat per poll cycle| REG
    REG -->|update latest row, no graph spam| PROJ
    PROJ --> REC
```

---

## Long-Run Workflow Test

The long-run workflow test is an opt-in diagnostic harness, not a production
command. It uses runtime workflow execution for each document and a bounded
daemon-like loop to observe background maintenance and projection state.

```mermaid
flowchart TD
    A["input/"] --> B["discover_pending"]
    B --> C["claim_document"]
    C --> D["processing/"]
    D --> E["token_check"]
    E --> F["parse_document"]
    F --> G["persist_document"]
    G --> H["enqueue_background_maintenance"]
    H --> I["observe_background_maintenance"]
    I --> J["verify_document_artifacts"]
    J --> K["move_completed"]
    K --> L["completed/"]

    E --> M["classify_failure"]
    F --> M
    G --> M
    I --> M
    J --> M
    M --> N["write_failure_record"]
    N --> O{"failure class"}
    O --> P["failed/"]
    O --> Q["quarantine/"]
    O --> R["continue_or_abort"]
    R --> B
    R --> S["abort snapshot"]
    S --> Q

    L --> T["post-doc maintenance drain\nmax 100 steps"]
    T --> U["projection/read checks"]
    U --> V["dump/final_report.md\nlongrun-dump.zip"]
```

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> CLAIMED
    CLAIMED --> TOKEN_CHECKED
    TOKEN_CHECKED --> PARSED
    PARSED --> PERSISTED
    PERSISTED --> MAINTENANCE_ENQUEUED
    MAINTENANCE_ENQUEUED --> MAINTENANCE_OBSERVED
    MAINTENANCE_OBSERVED --> COMPLETED

    TOKEN_CHECKED --> FAILED: token_count_out_of_range
    CLAIMED --> FAILED: document-specific failure
    PARSED --> FAILED: persist failed after retries
    MAINTENANCE_ENQUEUED --> FAILED: maintenance artifact missing

    CLAIMED --> QUARANTINED: systemic abort
    TOKEN_CHECKED --> QUARANTINED: suspicious repeated failure
    PARSED --> QUARANTINED: graph invariant corruption
    PERSISTED --> QUARANTINED: runtime worker stuck
```
