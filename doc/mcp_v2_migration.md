# MCP V2 Migration

LLM-Wiki 0.5 uses the official MCP Python SDK 2.x line:

```text
mcp>=2.2.0,<3
```

The application, Kogwistar core, and KG Doc Parser must use the same major
line. The Python SDK V1 decorators and request-context APIs are not supported;
wire compatibility is preserved instead. Existing MCP clients can continue to
use the supported protocol revisions over stdio, Streamable HTTP, and the
legacy SSE transport.

## Endpoints

- `/mcp` is the preferred Streamable HTTP endpoint.
- `/sse` and `/messages` remain available for older SSE clients and are
  deprecated.
- REST tool endpoints are unchanged.

Authentication and workspace or namespace authorization are evaluated for each
HTTP request. Tool visibility and argument validation remain enforced at the
application registry boundary; MCP V2 low-level handlers do not replace those
checks.

## Upgrade Order

1. Upgrade and merge Kogwistar core 0.5.0.
2. Update KG Doc Parser to 0.2.0 and regenerate `poetry.lock` and `req.txt`.
3. Pin both merged revisions in LLM-Wiki 0.5.0.
4. Run the full provider-free CI suites on CPython 3.12-3.14 and PyPy 3.11.

Do not pin a downstream repository to an unmerged feature branch for a release.
