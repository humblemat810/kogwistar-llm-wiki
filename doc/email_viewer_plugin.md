# Email Plugin And Viewer

LLM-Wiki treats email as an optional source plugin. Install
`kogwistar-email-plugin` separately, then use `EmailRuntime` to parse one
immutable RFC822 message and derive structural proposals.

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

Use `WorkbenchApi.view_email(...)` or the read-only MCP `email_view` tool to
render an authorized message. The viewer checks workspace and stream scope,
returns a structured text projection, escapes its HTML preview, and labels
structural proposals as pending. Email headers are evidence claims, not ACL
authority. The viewer does not return raw bytes or execute HTML.

Use `WorkbenchApi.propose_email_mapping(...)`, the read-only `email_propose`
tool, or `GET /api/email/proposal` to inspect the deterministic maintenance
patch. Applying it requires the explicit `email_accept`/`POST /api/email/accept`
confirmation. The existing maintenance patch validator enforces workspace
scope, provenance, idempotent IDs, and the invariant that raw source facts are
never rewritten.

For production, configure `SQLiteEmailEvidenceStore` (or another implementation
of `EmailEvidenceStore`) rather than the default in-memory store.

`EmailSyncService` provides bounded cursor synchronization. It calls a plugin
source adapter with the last snapshot, ingests all returned immutable messages,
and commits the next snapshot only after the whole batch succeeds. Failures
leave the prior snapshot in place so the batch can be replayed idempotently.
Use `SQLiteEmailSyncStateStore` with the plugin's snapshot class for
restart-safe local operation. Connector binding policy, ontology composition,
proposal acceptance, semantic search, and intelligence-memory promotion remain
later slices.
