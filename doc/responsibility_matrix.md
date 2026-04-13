# Kogwistar LLM-Wiki — Repository Architecture & Responsibility Model

## 1. Purpose

This document defines the **architectural decomposition and responsibility boundaries** across the four-repository system:

* `kogwistar` (substrate / engine)
* `kg-doc-parser` (ingestion / parsing)
* `kogwistar-obsidian-sink` (projection)
* `kogwistar-llm-wiki` (product / orchestration)

The goal is to:

* prevent responsibility drift
* preserve reusability of core libraries
* ensure clean dependency direction
* enable correct contract design (next phase)

---

## 2. Architectural Principles

### 2.1 Graph-Authoritative System

All state is:

* event-sourced
* append-only
* replayable

**Authority:** `kogwistar`

---

### 2.2 Projection, Not Duplication

External views (e.g., Obsidian):

* are rebuildable projections
* must not become sources of truth

**Authority:** `kogwistar-obsidian-sink`

---

### 2.3 Separation of Capability vs Policy

* **Capability** = reusable mechanism → lives in libraries
* **Policy** = product behavior → lives in app repo

Example:

* scheduler → `kogwistar`
* “when to merge nodes” → `kogwistar-llm-wiki`

---

### 2.4 Maintenance over Reasoning

Terminology standard:

* **Maintenance** = system-level knowledge curation and upkeep
* **Reasoning** = mechanism used inside some maintenance jobs

This avoids conflating:

* inference
* system policy
* execution outcomes

---

## 3. Repository Roles

---

## 3.1 `kogwistar`

### Role

**Authoritative substrate and execution engine**

### Responsibilities

#### Core Model

* graph primitives (nodes, edges, hyperedges)
* collections / namespaces
* artifact base types
* lifecycle states

#### Execution

* workflow runtime
* job scheduling primitives
* run tracking
* replay system

#### State Management

* append-only event model
* projections
* CDC / ChangeBus

#### Maintenance Capability (Generic)

* job state machine
* scheduling
* retry / backoff
* incremental recompute hooks
* provenance capture for maintenance runs

---

### Must NOT Own

* product UI
* ingestion UX
* Obsidian-specific rendering
* LLM-wiki policies
* consolidation heuristics
* promotion thresholds

---

### Dependency Rules

* no dependency on other repos
* bottom of dependency graph

---

### Authority

* graph truth
* event history
* workflow execution history

---

## 3.2 `kg-doc-parser`

### Role

**Document ingestion and parsing boundary**

### Responsibilities

#### Source Acquisition

* file loaders
* PDF / OCR pipelines
* source normalization

#### Structure Extraction

* sections / pages / headings
* tables / layout
* fragment generation
* source maps

#### Semantic Extraction (Document-Scoped)

* entity extraction
* citation extraction
* section summaries
* source-native links

#### Ingestion Hygiene

* pointer repair
* OCR fallback
* parse retry logic
* fragment normalization

---

### Must NOT Own

* global knowledge curation
* cross-document merge policy
* KG promotion logic
* product UI
* projection logic
* maintenance policy

---

### Dependency Rules

* depends on `kogwistar`
* no dependency on sink or app repo

---

### Authority

* correctness of parsed document structure
* source-native extraction outputs

---

## 3.3 `kogwistar-obsidian-sink`

### Role

**Projection adapter (graph → Obsidian vault)**

### Responsibilities

#### Projection

* entity → note mapping
* edge → link mapping
* canvas generation

#### Materialization

* markdown rendering
* frontmatter generation
* file path mapping

#### Vault Maintenance

* file CRUD
* drift detection
* rebuild (full + incremental)

#### CDC Consumption

* event subscription
* changed-entity refresh
* batching / debounce

---

### Must NOT Own

* graph truth
* ingestion
* maintenance policy
* promotion decisions
* product UI

---

### Dependency Rules

* depends on `kogwistar`
* no dependency on parser or app repo

---

### Authority

* correctness of Obsidian projection

---

## 3.4 `kogwistar-llm-wiki`

### Role

**Product layer, orchestration, maintenance brain**

### Responsibilities

#### Product Orchestration

* workspace lifecycle
* source registration
* ingestion coordination

#### User Experience

* web UI
* dashboards
* review flows
* explainability panels

#### Maintenance Policy (Core)

##### Cross-link Maintenance

* candidate generation
* validation
* promotion decisions

##### Entity Maintenance

* dedupe
* merge policy
* alias resolution

##### Evidence Maintenance

* support validation
* stale detection

##### Contradiction Maintenance

* conflict detection
* grouping
* review workflows

##### Conversation → KG Promotion

* promotion thresholds
* gating rules
* review processes

##### Projection Triggers

* when to refresh notes
* batching policies

##### Wisdom Extraction

* derive reusable lessons from execution
* attach to provenance

---

#### Agent System

* hot-path ingestion agents
* cold-path consolidation agents
* idle-time scheduling policy
* prioritization rules

---

#### Deployment

* environment config
* orchestration setup
* demo setups

---

### Must NOT Own

* event engine internals
* parsing internals
* projection internals
* generic reusable primitives

---

### Dependency Rules

* depends on all other repos
* top of dependency graph

---

### Authority

* product behavior
* maintenance policy
* promotion rules
* UX

---

## 4. Dependency Architecture

```
kogwistar (core)
   ↑        ↑
   │        │
kg-doc-parser   kogwistar-obsidian-sink
        ↑
        │
kogwistar-llm-wiki (app)
```

Rules:

* no upward dependency
* no lateral dependency between parser and sink
* app is the only composer

---

## 5. Maintenance Architecture

### 5.1 Responsibility Split

| Layer                     | Responsibility                  |
| ------------------------- | ------------------------------- |
| `kogwistar`               | maintenance capability (engine) |
| `kg-doc-parser`           | document-level repair           |
| `kogwistar-obsidian-sink` | projection upkeep               |
| `kogwistar-llm-wiki`      | knowledge maintenance policy    |

---

### 5.2 Maintenance Definition

Maintenance includes:

* deduplication
* merge decisions
* link validation
* contradiction detection
* synthesis refresh
* promotion decisions
* projection refresh triggers

---

### 5.3 Reasoning Role

* reasoning is **used by maintenance jobs**
* reasoning is **not the architectural layer**

---

## 6. Authority Matrix

| Concern              | Owner                   |
| -------------------- | ----------------------- |
| Graph truth          | kogwistar               |
| Event history        | kogwistar               |
| Workflow runs        | kogwistar               |
| Parsed structure     | kg-doc-parser           |
| Obsidian vault       | kogwistar-obsidian-sink |
| Maintenance behavior | kogwistar-llm-wiki      |
| Promotion rules      | kogwistar-llm-wiki      |
| UX / workflows       | kogwistar-llm-wiki      |

---

## 7. Feature Placement Rules

### Place in `kogwistar` if:

* generic
* reusable
* substrate-level

### Place in `kg-doc-parser` if:

* document-scoped
* parsing-related

### Place in `kogwistar-obsidian-sink` if:

* projection-specific

### Place in `kogwistar-llm-wiki` if:

* product behavior
* policy
* user workflow
* maintenance logic

---

## 8. Forbidden Ownership

| Repo          | Must NOT Become     |
| ------------- | ------------------- |
| kogwistar     | product app         |
| kg-doc-parser | orchestration layer |
| obsidian-sink | truth store         |
| llm-wiki      | engine duplication  |

---

## 9. Key Architectural Insight

The system is layered:

* **Substrate** (kogwistar)
* **Ingestion** (parser)
* **Projection** (sink)
* **Product / Maintenance Brain** (llm-wiki)

The most critical separation:

> Maintenance capability belongs to the substrate
> Maintenance behavior belongs to the product

---

## 10. Next Step

With responsibilities fixed, the next document should define:

**Inter-repo contracts**

* APIs
* event schemas
* ingestion payloads
* projection contracts
* maintenance job interfaces

---

END
