# Email Plugin And Viewer

LLM-Wiki treats email as an optional source plugin. Install
`kogwistar-email-plugin` separately, then use `EmailRuntime` to parse one
immutable RFC822 message and derive structural proposals.

For a local source checkout, install the sibling plugin explicitly:

```powershell
python -m pip install -e .\kogwistar-email-plugin[core]
```

The plugin is intentionally not bundled into the base LLM-Wiki image. Root
unit tests that exercise the cross-repository parser path skip when the
optional package is absent; the plugin repository owns the parser, adapter,
GreenMail, and runtime-matrix CI for that package.

## Runtime Boundaries

```mermaid
flowchart LR
    MAIL["IMAP / Maildir / mbox"] --> ADAPTER["email plugin\nsource adapter"]
    ADAPTER -->|authorized stream| RUNTIME["LLM-Wiki EmailRuntime"]
    RUNTIME -->|exact bytes| EVIDENCE["immutable evidence\nSQLiteEmailEvidenceStore"]
    RUNTIME -->|display text only| SOURCE["source revision pipeline"]
    RUNTIME -->|structural mapping| PROPOSAL["pending mapping artifact"]
    PROPOSAL --> VIEW["ACL-checked viewer\nREST / MCP / browser"]
    PROPOSAL --> REVIEW["review state\npending / accepted / needs_review"]
    REVIEW -->|explicit confirmed=true| PATCH["maintenance patch fence"]
    PATCH --> GRAPH["curated graph\nexisting Kogwistar namespaces"]
    PATCH -. never mutates .-> EVIDENCE
```

The solid path is the product data flow. The dotted constraint is a source
truth invariant: parsing, mapping, maintenance, and the viewer may derive or
display interpretations, but none may rewrite the original RFC822 bytes.

```python
from kogwistar_llm_wiki.email import EmailIngestRequest, EmailRuntime, SQLiteEmailEvidenceStore

runtime = EmailRuntime(
    pipeline=pipeline,
    store=SQLiteEmailEvidenceStore("email-evidence.sqlite3"),
    authorize_stream=lambda workspace_id, stream_id: connector_acl.allows(
        workspace_id, stream_id
    ),
)
result = runtime.ingest(
    EmailIngestRequest(
        workspace_id="acme",
        stream_id="mailbox-stream-id",
        source_key="uid:123",
        source_revision_id="imap:uidvalidity:123",
        raw_bytes=raw_rfc822_bytes,
    )
)
```

`EmailRuntime` stores the exact bytes and parser/mapping payloads under the
workspace and source revision. It registers only the display text through the
existing immutable source-revision pipeline and writes an internal
`email_structural_mapping` evidence artifact with `acceptance_status=pending`.
It never creates accepted knowledge, memories, or graph relations.
The evidence stores also verify that the request identity, raw-byte SHA-256,
and `blob_ref` agree before accepting a new record; legacy rows remain
readable, but new writes cannot introduce an ungrounded digest.

Use `WorkbenchApi.view_email(...)` or the read-only MCP `email_view` tool to
render an authorized message. The viewer checks workspace and stream scope,
returns a structured text projection, escapes its HTML preview, and labels
structural proposals with their review state (`pending`, `accepted`, or
`needs_review`). Review state is mutable app-owned state and is kept separate
from immutable evidence. Email headers are evidence claims, not ACL authority.
The viewer does not return raw bytes or execute HTML.

The browser workbench exposes the same projection through the **Email evidence**
panel. It accepts `workspace_id` from the active workspace and asks the user
for the stream and immutable source revision. The panel renders the returned
`body_text` as text, never with `dangerouslySetInnerHTML`, and only enables
acceptance after a source document ID is supplied.

The standalone browser shell is available at `GET /email/viewer`. It contains
no message data, uses same-origin fetches to `/api/email/view`, and renders all
returned values with DOM `textContent`. The response sets a restrictive CSP,
`X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`; mailbox
authorization remains enforced by the JSON endpoint for every lookup.

After loading evidence, the shell exposes two explicit user actions: **Confirm
mapping acceptance** calls `POST /api/email/accept` with `confirmed=true`, and
**Confirm memory promotion** calls `POST /api/email/memory/promote` with the
user-entered statement and `confirmed=true`. Neither action is automatic, and
the promotion button remains disabled until the mapping is accepted. The
server revalidates workspace, stream, immutable source-document, provenance,
and review state on every request.

```mermaid
flowchart LR
    FORM["stream + revision + source document"] --> VIEW["GET /api/email/view"]
    VIEW --> PANEL["safe browser panel"]
    PANEL -->|pending| CONFIRM["Accept mapping"]
    CONFIRM --> POST["POST /api/email/accept\nconfirmed=true"]
    POST --> STATUS["refresh viewer + show outcome"]
    STATUS -->|accepted retry| IDEMP["stored result\nno duplicate patch"]
```

Use `WorkbenchApi.propose_email_mapping(...)`, the read-only `email_propose`
tool, or `GET /api/email/proposal` to inspect the deterministic maintenance
patch. Applying it requires the explicit `email_accept`/`POST /api/email/accept`
confirmation. The existing maintenance patch validator enforces workspace
scope, provenance, idempotent IDs, and the invariant that raw source facts are
never rewritten. An accepted mapping is recorded by
`InMemoryEmailReviewStateStore` by default, or by
`SQLiteEmailReviewStateStore` for restart-safe operation. Repeating an
accepted request returns the stored result without applying the patch again.

For production, configure `SQLiteEmailEvidenceStore` (or another implementation
of `EmailEvidenceStore`) rather than the default in-memory store.

`EmailSyncService` provides bounded cursor synchronization. It calls a plugin
source adapter with the last snapshot, ingests all returned immutable messages,
and commits the next snapshot only after the whole batch succeeds. Failures
leave the prior snapshot in place so the batch can be replayed idempotently.
Use `SQLiteEmailSyncStateStore` with the plugin's snapshot class for
restart-safe local operation. Connector binding policy, ontology composition,
semantic search, and intelligence-memory promotion remain later slices.

For a long-running deployment, inject `EmailSyncJobScheduler` into
`WorkbenchApi` and submit a bounded cycle through
`POST /api/email/sync/enqueue`. The request contains only workspace,
connector, cycle, owner, and retry/lease limits; it never carries a password,
token, or credential value. A daemon or operator-owned runner claims the
workspace-scoped queue item and resolves the opaque credential reference only
inside the configured plugin adapter factory.

```mermaid
sequenceDiagram
    participant C as REST client or operator
    participant W as Workbench API
    participant Q as Kogwistar email-sync queue
    participant D as Email job runner
    participant P as Plugin adapter
    participant E as Immutable evidence + cursor stores

    C->>W: enqueue workspace, connector, cycle
    W->>Q: persist secret-free bounded job
    D->>Q: claim with lease
    D->>P: resolve opaque credential reference locally
    P-->>D: messages + mailbox events
    D->>E: persist evidence/events, then cursor
    D->>Q: acknowledge or retry/fail
```

Email memories retain their source stream ACL in the bounded memory metadata
envelope. Generic memory recall and review hide ACL-bearing records by default;
callers must provide stream IDs that the email authorizer approves before those
records become visible. A workspace match alone never widens a mailbox scope.
When a memory is derived from multiple messages, the caller supplies aligned
`stream_ids` and `source_revision_ids`; the server authorizes every stream and
stores all source-document IDs. It never widens the memory ACL to the union of
unverified streams.

## Review And Acceptance Sequence

```mermaid
sequenceDiagram
    participant U as User or Codex
    participant V as Viewer API
    participant S as Evidence store
    participant R as Review state
    participant M as Maintenance fence
    participant G as Curated graph

    U->>V: email_view(workspace, stream, revision)
    V->>S: read immutable evidence
    V->>R: read mapping status
    R-->>V: pending / accepted / needs_review
    V-->>U: escaped text + proposals + status
    U->>V: email_accept(confirmed=false)
    V->>R: persist pending review
    V-->>U: confirmation_required + patch preview
    U->>V: email_accept(confirmed=true)
    V->>M: validate scope, provenance, raw-fact fence
    M->>G: apply deterministic patch
    M-->>V: applied or needs_review result
    V->>R: persist final review state
    V-->>U: result; retries are idempotent
```
