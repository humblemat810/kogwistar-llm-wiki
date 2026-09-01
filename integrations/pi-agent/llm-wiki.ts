/**
 * pi extension for the llm-wiki grounded agent gateway.
 * Install into ~/.pi/agent/extensions/ or .pi/extensions/.
 */
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const BASE_URL = (process.env.LLM_WIKI_BASE_URL ?? "http://127.0.0.1:8765").replace(/\/$/, "");
const WORKSPACE = process.env.LLM_WIKI_WORKSPACE ?? "default";

async function callTool(name: string, arguments_: Record<string, unknown>, signal?: AbortSignal) {
  const response = await fetch(`${BASE_URL}/mcp/tools/call`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, arguments: arguments_ }),
    signal,
  });
  const body = await response.json() as { structuredContent?: unknown; content?: unknown };
  if (!response.ok) throw new Error(JSON.stringify(body));
  return body.structuredContent ?? body.content ?? body;
}

function result(value: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }] };
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "llm_wiki_ask",
    label: "llm-wiki ask",
    description: "Ask llm-wiki a grounded question over a bounded graph lens. Inspect citations and proposals before acting.",
    parameters: Type.Object({ query_text: Type.String(), session_id: Type.Optional(Type.String()) }),
    async execute(_toolCallId, params, signal) {
      return result(await callTool("llm_wiki.ask", { workspace_id: WORKSPACE, query_text: params.query_text, session_id: params.session_id ?? "pi" }, signal));
    },
  });

  pi.registerTool({
    name: "llm_wiki_search",
    label: "llm-wiki search",
    description: "Retrieve a bounded graph lens with nodes, edges, grounding, and selection explanations.",
    parameters: Type.Object({ query_text: Type.String(), hop_limit: Type.Optional(Type.Integer({ minimum: 0, maximum: 8 })), max_nodes: Type.Optional(Type.Integer({ minimum: 1, maximum: 200 })) }),
    async execute(_toolCallId, params, signal) {
      return result(await callTool("llm_wiki.search", { workspace_id: WORKSPACE, query_text: params.query_text, hop_limit: params.hop_limit ?? 1, max_nodes: params.max_nodes ?? 40 }, signal));
    },
  });

  pi.registerTool({
    name: "llm_wiki_propose",
    label: "llm-wiki validate proposal",
    description: "Validate a graph proposal without changing the graph. A no_change outcome is valid.",
    parameters: Type.Object({ request: Type.Record(Type.String(), Type.Unknown()), proposal: Type.Record(Type.String(), Type.Unknown()) }),
    async execute(_toolCallId, params, signal) {
      return result(await callTool("llm_wiki.propose", params, signal));
    },
  });

  pi.registerTool({
    name: "llm_wiki_confirm",
    label: "llm-wiki confirm proposal",
    description: "Explicitly confirm one previously reviewed proposal. Never call this implicitly after an LLM suggestion.",
    parameters: Type.Object({ interaction_id: Type.String(), confirmed: Type.Boolean(), proposal: Type.Optional(Type.Record(Type.String(), Type.Unknown())) }),
    async execute(_toolCallId, params, signal) {
      return result(await callTool("llm_wiki.confirm", { workspace_id: WORKSPACE, interaction_id: params.interaction_id, confirmed: params.confirmed, proposal: params.proposal }, signal));
    },
  });

  pi.registerCommand("llm-wiki", {
    description: "Check the llm-wiki agent gateway",
    handler: async (_args, ctx) => {
      ctx.ui.notify(`llm-wiki configured at ${BASE_URL} (workspace ${WORKSPACE})`, "info");
    },
  });
}
