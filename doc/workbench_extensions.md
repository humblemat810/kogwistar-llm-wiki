# Optional Workbench Extensions

LLM-Wiki accepts trusted, locally installed application extensions through the
`kogwistar_llm_wiki.workbench_extensions` Python entry-point group. This is a
host integration boundary, not a marketplace or sandbox for untrusted code.
Install only extensions whose code and dependencies the operator trusts.

An extension factory receives the host `WorkbenchApi` and returns a
`WorkbenchExtension`. Every route must use the extension's namespace:

```text
/plugins/<extension_id>/<route>
```

Routes explicitly declare `GET` or `POST`, required `read` or `write` scope,
and a function that extracts the target workspace from query/body data. The
HTTP host authenticates and authorizes that scope/workspace before invoking the
handler. The handler receives the authenticated principal, workspace,
read-only query mapping, and JSON payload. HTML responses use the host's
security headers; JSON is the default response type.

Installing a distribution never activates its entry point. The combined server
loads only IDs named in `LLM_WIKI_WORKBENCH_EXTENSIONS`, comma-separated:

```text
LLM_WIKI_WORKBENCH_EXTENSIONS=extension-a,extension-b
```

An empty setting loads no extension code. A configured but missing extension,
duplicate ID, route collision, or invalid namespace fails server startup rather
than silently disabling the requested integration.

Host workspace authorization is necessary but not sufficient for resources
inside an extension. Extensions must still enforce their resource ACLs (for
example, authorization for an externally connected source) before reading or
mutating data.
Kogwistar remains canonical graph authority; extension handlers must use
application services and must not create a parallel graph truth.
