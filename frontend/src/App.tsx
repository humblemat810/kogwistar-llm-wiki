import { useEffect, useRef, useState } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import type { LensNode, LensSnapshot, WorkbenchAskResponse, WorkbenchInteraction } from "./contracts";
import { sampleLens } from "./contracts";

type Mode = "deterministic" | "codex";

type LensLoad = { snapshot: LensSnapshot; source: "live" | "offline" };
type AskLoad = { response: WorkbenchAskResponse; source: "live" | "offline" };

const workspaceId = new URLSearchParams(window.location.search).get("workspace_id") || "rl-fixture";

async function loadLens(query: string, anchorId?: string, pinnedNodeIds: string[] = [], allowOffline = true): Promise<LensLoad> {
  const params = new URLSearchParams({ workspace_id: workspaceId, query });
  if (anchorId) params.set("explicit_anchor_id", anchorId);
  for (const nodeId of pinnedNodeIds) params.append("pinned_node_id", nodeId);
  const endpoint = `/api/lens?${params.toString()}`;
  try {
    const response = await fetch(endpoint, { headers: { accept: "application/json" } });
    if (response.ok) return { snapshot: (await response.json()) as LensSnapshot, source: "live" };
    if (!allowOffline) throw new Error(`Lens request failed (${response.status})`);
  } catch (error) {
    if (!allowOffline) throw error;
    // Offline fixture is intentional for local UI development and CI.
  }
  return { snapshot: { ...sampleLens, projected_at_ms: Date.now() }, source: "offline" };
}

async function askWorkbench(
  query: string,
  mode: Mode,
  sessionId: string,
  pinnedNodeIds: string[] = [],
  signal?: AbortSignal,
): Promise<AskLoad> {
  try {
    const endpoint = mode === "codex" ? "/api/interactions" : "/api/ask";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      signal,
      body: JSON.stringify({
        workspace_id: workspaceId,
        query_text: query,
        mode,
        session_id: sessionId,
        pinned_node_ids: pinnedNodeIds,
      }),
    });
    if (response.ok && mode === "deterministic") {
      return { response: (await response.json()) as WorkbenchAskResponse, source: "live" };
    }
    if (response.ok && mode === "codex") {
      let interaction = (await response.json()) as WorkbenchInteraction;
      for (let attempt = 0; attempt < 120 && interaction.status === "pending"; attempt += 1) {
        await new Promise<void>((resolve, reject) => {
          const timer = window.setTimeout(resolve, 500);
          signal?.addEventListener("abort", () => { window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
        });
        const params = new URLSearchParams({ workspace_id: interaction.workspace_id, interaction_id: interaction.interaction_id });
        const polled = await fetch(`/api/interactions?${params.toString()}`, { headers: { accept: "application/json" }, signal });
        if (!polled.ok) throw new Error(`Interaction polling failed (${polled.status})`);
        interaction = (await polled.json()) as WorkbenchInteraction;
      }
      if (interaction.status === "completed" && interaction.response) {
        return { response: { ...interaction.response, interaction_id: interaction.interaction_id }, source: "live" };
      }
      if (interaction.status === "failed") throw new Error(interaction.error ?? "Codex interaction failed");
      throw new Error("Codex interaction did not complete within 60 seconds");
    }
    if (mode === "codex") {
      throw new Error(`Workbench request failed (${response.status})`);
    }
    if (response.status !== 404 && response.status !== 503) {
      throw new Error(`Workbench request failed (${response.status})`);
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (mode === "codex") throw error;
    // The offline fixture keeps local UI and interaction regression usable.
  }
  const labels = sampleLens.nodes.map((node) => node.label).join(", ");
  return {
    source: "offline",
    response: {
      mode,
      agent_status: "not_requested",
      answer: {
        text: `Offline fixture context contains: ${labels}.`,
        outcome: "answer",
        lens_id: sampleLens.lens_id,
        source_watermark: sampleLens.source_watermark,
        cited_entity_ids: sampleLens.nodes.map((node) => node.id),
        insufficiency_reason: null,
      },
      snapshot: { ...sampleLens, projected_at_ms: Date.now() },
      history: { id: "offline", session_id: sessionId, lens_id: sampleLens.lens_id, outcome: "answer", created_at_ms: Date.now() },
    },
  };
}

function nodeColor(node: LensNode, selected: boolean): string {
  if (selected) return "#f5b94c";
  if (node.node_type === "risk") return "#e97872";
  if (node.node_type === "method") return "#72c7c1";
  return "#9baed0";
}

export function App() {
  const graphRoot = useRef<HTMLDivElement>(null);
  const renderer = useRef<Sigma | null>(null);
  const positions = useRef<Map<string, { x: number; y: number }>>(new Map());
  const [lens, setLens] = useState<LensSnapshot>(sampleLens);
  const [query, setQuery] = useState("Why do verifiable rewards help?");
  const [mode, setMode] = useState<Mode>("deterministic");
  const [selectedId, setSelectedId] = useState<string>(sampleLens.nodes[0]?.id ?? "");
  const [pinned, setPinned] = useState<Set<string>>(new Set());
  const [anchorId, setAnchorId] = useState<string | undefined>(undefined);
  const [lensSource, setLensSource] = useState<"live" | "offline">("offline");
  const [loading, setLoading] = useState(false);
  const [proposalStatus, setProposalStatus] = useState<string>("");
  const [cockpitProposal, setCockpitProposal] = useState<Record<string, unknown> | null>(null);
  const [cockpitProposalRequest, setCockpitProposalRequest] = useState<Record<string, unknown> | null>(null);
  const [cockpitInteractionId, setCockpitInteractionId] = useState<string | null>(null);
  const [answer, setAnswer] = useState<WorkbenchAskResponse["answer"] | null>(null);
  const [agentStatus, setAgentStatus] = useState<WorkbenchAskResponse["agent_status"]>("not_requested");
  const [requestError, setRequestError] = useState("");
  const sessionId = useRef(`browser-${Math.random().toString(36).slice(2)}`);
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!graphRoot.current) return;
    const graph = new Graph();
    for (const [index, node] of lens.nodes.entries()) {
      const previous = positions.current.get(node.id);
      const seed = index === 0
        ? { x: 2.4, y: 0 }
        : index === 1
          ? { x: -0.8, y: 0.85 }
          : { x: -0.8, y: -0.85 };
      const position = previous ?? seed;
      positions.current.set(node.id, position);
      graph.addNode(node.id, {
        label: node.label,
        size: node.id === selectedId ? 13 : 9,
        color: nodeColor(node, node.id === selectedId),
        x: position.x,
        y: position.y,
      });
    }
    for (const edge of lens.edges) {
      if (edge.source_ids.length !== 1 || edge.target_ids.length !== 1) continue;
      const source = edge.source_ids[0];
      const target = edge.target_ids[0];
      if (!graph.hasNode(source) || !graph.hasNode(target)) continue;
      graph.addEdgeWithKey(edge.id, source, target, { label: edge.relation, color: "#44536c", size: 2 });
    }
    // Hyperedges remain relation hubs in the display; they are never expanded
    // into implied pairwise facts.
    for (const hyperedge of lens.hyperedges) {
      const hub = `hyperedge:${hyperedge.id}`;
      const hubPosition = positions.current.get(hub) ?? { x: 0, y: 0 };
      positions.current.set(hub, hubPosition);
      const hubLabel = hyperedge.relation.length > 16
        ? hyperedge.relation.split(/\s+/)[0]
        : hyperedge.relation;
      graph.addNode(hub, { label: hubLabel, size: 7, color: "#d39a62", x: hubPosition.x, y: hubPosition.y });
      for (const member of [...hyperedge.source_ids, ...hyperedge.target_ids]) {
        if (graph.hasNode(member)) graph.addEdgeWithKey(`${hub}:${member}`, hub, member, { color: "#805f40", size: 1 });
      }
    }
    for (const node of graph.nodes()) {
      const attributes = graph.getNodeAttributes(node);
      positions.current.set(node, { x: attributes.x as number, y: attributes.y as number });
    }
    renderer.current?.kill();
    const nextRenderer = new Sigma(graph, graphRoot.current, {
      renderLabels: true,
      labelFont: "ui-sans-serif",
      labelColor: { color: "#dce8f9" },
      defaultEdgeColor: "#44536c",
      // Keep labels inside the canvas on narrow viewports as well as desktop.
      stagePadding: 96,
      labelDensity: 0.6,
      allowInvalidContainer: true,
    });
    renderer.current = nextRenderer;
    nextRenderer.on("clickNode", ({ node }) => {
      if (node.startsWith("hyperedge:")) return;
      void expandNode(node);
    });
    return () => {
      nextRenderer.kill();
      if (renderer.current === nextRenderer) renderer.current = null;
    };
  }, [lens, selectedId, pinned]);

  const selected = lens.nodes.find((node) => node.id === selectedId) ?? lens.nodes[0];
  const explanation = lens.selection_explanations.find((item) => item.node_id === selected?.id);

  async function submitQuery(nextQuery = query) {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setLoading(true);
    setRequestError("");
    try {
      const loaded = await askWorkbench(nextQuery, mode, sessionId.current, Array.from(pinned), controller.signal);
      if (controller.signal.aborted) return;
      setLens(loaded.response.snapshot);
      setLensSource(loaded.source);
      setAnswer(loaded.response.answer);
      setCockpitProposal(loaded.response.answer.proposal ?? null);
      setCockpitProposalRequest(loaded.response.proposal_request ?? null);
      setCockpitInteractionId(loaded.response.interaction_id ?? null);
      setAgentStatus(loaded.response.agent_status);
      setSelectedId(loaded.response.snapshot.nodes[0]?.id ?? "");
      setAnchorId(undefined);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setRequestError(error instanceof Error ? error.message : "Workbench request failed");
      }
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null;
        setLoading(false);
      }
    }
  }

  async function expandNode(nodeId: string) {
    setSelectedId(nodeId);
    setAnchorId(nodeId);
    setLoading(true);
    try {
      const loaded = await loadLens(query, nodeId, Array.from(pinned), mode !== "codex");
      setLens(loaded.snapshot);
      setLensSource(loaded.source);
      setSelectedId(nodeId);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Lens request failed");
    } finally {
      setLoading(false);
    }
  }

  function askFollowup(nextQuery: string) {
    setQuery(nextQuery);
    void submitQuery(nextQuery);
  }

  function togglePin() {
    if (!selected) return;
    setPinned((current) => {
      const next = new Set(current);
      if (next.has(selected.id)) next.delete(selected.id); else next.add(selected.id);
      return next;
    });
  }

  async function validateProposal() {
    if (!selected) return;
    try {
      const response = await fetch("/api/proposal/validate", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({
          request: cockpitProposalRequest ?? {
            workspace_id: lens.workspace_id,
            query_text: query,
            source_watermark: lens.source_watermark,
            explicit_anchor_ids: anchorId ? [anchorId] : [],
            pinned_node_ids: Array.from(pinned),
          },
          proposal: { lens_id: lens.lens_id, source_watermark: lens.source_watermark, operation: "review", target_ids: [selected.id], evidence_ids: [selected.id] },
        }),
      });
      if (!response.ok) { setProposalStatus("Host validation endpoint unavailable"); return; }
      const result = (await response.json()) as { accepted: boolean; reason: string };
      setProposalStatus(result.accepted ? "Ready for explicit confirmation" : `Not ready: ${result.reason}`);
    } catch {
      setProposalStatus("Host validation endpoint unavailable");
    }
  }

  async function confirmCockpitProposal() {
    if (!cockpitProposal) return;
    try {
      const response = await fetch("/api/proposal/confirm", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({
          confirmed: true,
          workspace_id: lens.workspace_id,
          interaction_id: cockpitInteractionId,
          proposal: cockpitProposal,
        }),
      });
      const result = (await response.json()) as { status?: string; reason?: string; lens?: LensSnapshot };
      if (!response.ok || result.status === "rejected") {
        setProposalStatus(`Proposal not applied: ${result.reason ?? "host rejected it"}`);
        return;
      }
      setProposalStatus(`Proposal ${result.status ?? "applied"}`);
      if (result.status === "applied") {
        if (result.lens) setLens(result.lens);
        setCockpitProposal(null);
      }
    } catch {
      setProposalStatus("Could not reach the host confirmation endpoint");
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div><p className="eyebrow">KOGWISTAR / KNOWLEDGE WORKBENCH</p><h1>Think in a living graph.</h1></div>
        <div className="status"><span className="dot" /> {lens.nodes.length} visible / {lens.completeness} / <span className={lensSource === "live" ? "source-live" : "source-offline"}>{lensSource === "live" ? "live graph" : "offline fixture"}</span></div>
      </header>
      <section className="idea-bar" aria-label="Knowledge query controls">
        <label htmlFor="query">Ask the graph</label>
        <input id="query" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submitQuery(); }} />
        <button className="primary" onClick={() => void submitQuery()} disabled={loading}>{loading ? "Following..." : "Explore"}</button>
        <select value={mode} onChange={(event) => setMode(event.target.value as Mode)} aria-label="Orchestration mode">
          <option value="deterministic">Deterministic workflow</option><option value="codex">Codex cockpit (review-first)</option>
        </select>
      </section>
      {answer && <section className="answer-panel" aria-live="polite">
        <p className="eyebrow">Investigation response</p>
        <p>{answer.text}</p>
        <small>{answer.outcome} / {answer.cited_entity_ids.length} cited entities{mode === "codex" ? ` / agent ${agentStatus.replace("_", " ")}` : ""}</small>
        {cockpitProposal && <div className="actions"><button className="primary" onClick={() => void confirmCockpitProposal()}>Confirm proposed graph change</button><button onClick={() => { setCockpitProposal(null); setProposalStatus("Proposal dismissed; graph unchanged"); }}>Dismiss proposal</button></div>}
        {proposalStatus && <p className="why" role="status">{proposalStatus}</p>}
      </section>}
      {requestError && <section className="answer-panel" role="alert"><p className="eyebrow">Workbench error</p><p>{requestError}</p></section>}
      <section className="workspace">
        <div className="graph-panel"><div className="graph-shell"><div ref={graphRoot} className="graph" aria-label="Bounded knowledge graph" role="img" />{lens.nodes.length === 0 && <p className="empty-lens" role="status">No grounded candidates in this scoped lens.</p>}</div><div className="graph-caption"><span>Watermark {String(lens.source_watermark ?? "unavailable")}</span><span>{lens.query_timing_ms}ms lens</span><span>{pinned.size} pinned</span></div></div>
        <aside className="evidence" aria-label="Evidence drawer">
          <div className="panel-heading"><span>Evidence drawer</span><button onClick={togglePin}>{selected && pinned.has(selected.id) ? "Unpin" : "Pin"}</button></div>
          {selected ? <>
            <p className="kind">{selected.node_type} / {selected.graph_space}</p><h2>{selected.label}</h2>
            <p className="why">{explanation?.reason ?? "retained"}{explanation?.distance != null ? ` / ${explanation.distance} hop` : ""}</p>
            <h3>Grounding</h3><div className="grounding">{selected.grounding.map((item, index) => <div className="citation" key={index}><strong>{String(item.doc_id ?? "source")}</strong><span>{String(item.excerpt ?? "Evidence span unavailable")}</span></div>)}</div>
            <h3>Next action</h3><div className="actions"><button onClick={() => askFollowup(`What supports ${selected.label}?`)}>Find support</button><button onClick={() => askFollowup(`What could contradict ${selected.label}?`)}>Find tension</button><button onClick={() => void validateProposal()}>Validate proposal</button>{proposalStatus && <p className="why" role="status">{proposalStatus}</p>}</div>
          </> : <p>No grounded item selected.</p>}
        </aside>
      </section>
      <section className="accessible-list" aria-label="Accessible graph item list"><h2>Visible claims</h2><div className="claim-grid">{lens.nodes.map((node) => <button className={node.id === selected?.id ? "claim active" : "claim"} key={node.id} onClick={() => void expandNode(node.id)}><span>{node.label}</span><small>{node.node_type} / {node.grounding.length} citation{node.grounding.length === 1 ? "" : "s"}</small></button>)}</div></section>
    </main>
  );
}
