# Codex host integration

The Codex executable stays on the host. Run llm-wiki in Docker and connect
Codex to its native MCP endpoint or REST gateway; do not install Codex inside
the llm-wiki image.

For an MCP-capable Codex client, configure this remote server:

```text
http://127.0.0.1:8780/mcp
```

Set the same bearer token configured by `LLM_WIKI_API_TOKEN` in the client
configuration. For a host-driven cockpit, configure the container with
`LLM_WIKI_COCKPIT_CALLBACK_URL` and explicitly allow its hostname through
`LLM_WIKI_COCKPIT_CALLBACK_ALLOWED_HOSTS`. The callback receives a bounded
request, semantic-lens snapshot, and prior observations, and returns a
validated cockpit action. Graph mutations remain behind explicit confirmation.

## Calling Codex App Server From LLM-Wiki

The workbench can optionally use the installed Codex App Server protocol for
each bounded turn instead of the `codex exec` protocol:

```text
llm-wiki workbench --workspace personal --codex-transport app_server
```

The equivalent environment setting is:

```text
KOGWISTAR_CODEX_TRANSPORT=app_server
```

`exec` remains the default. The App Server adapter uses its stdio JSON-RPC
transport, sends `initialize`, `thread/start`, and `turn/start`, forwards
streamed assistant deltas to workbench progress and trace callbacks, and
requests the typed output schema for cockpit turns. It uses an ephemeral,
read-only thread with `approvalPolicy=never`; Codex cannot write the graph or
host filesystem through this adapter. Timeouts terminate the child and close
its pipes to avoid dangling Codex processes.

The configured `--codex-model`, `--codex-profile`, and `--codex-timeout`
settings also apply to this transport. The profile is passed as an App Server
configuration override. If an installed Codex version rejects that override,
use the server's configured default profile or the `exec` transport.

The current adapter starts one isolated App Server child for each bounded
turn; it does not attach to an already-running remote App Server. This keeps
thread lifetime, timeout cleanup, and workbench lease ownership explicit.
This is a local-process integration, not a new LLM-Wiki HTTP or MCP endpoint.
For a separately hosted Codex service, use an explicit host-side bridge and
the existing cockpit callback allowlist rather than exposing a remote command
channel.

The semantic MCP tools are `query`, `search`, `ingest`, `source`, `reingest`,
`maintain`, `status`, `hypergraph_search`, `history`, `propose`, and `confirm`.
Use `propose` followed by explicit `confirm`; there is no direct graph-write
tool. Configure `LLM_WIKI_MCP_TOKEN` and `LLM_WIKI_MCP_TOKEN_SCOPES` for the
remote MCP transport.

For a trusted local personal deployment, set `LLM_WIKI_AUTH_MODE=disabled` and
omit the bearer token. The server then has no authenticated principal or ACL
requirement, but the configured workspace remains the data boundary. This mode
is not suitable for a shared host; use static-token or JWT mode when another
user or network can reach the service.
