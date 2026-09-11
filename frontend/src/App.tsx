import { useEffect, useRef, useState } from "react";
import { MultiDirectedGraph } from "graphology";
import Sigma from "sigma";
import type { LensNode, LensSnapshot, SettingsSnapshot, WorkbenchAskResponse, WorkbenchInteraction } from "./contracts";
import { sampleLens, sampleSettings } from "./contracts";

type Mode = "deterministic" | "codex";

type LensLoad = { snapshot: LensSnapshot; source: "live" | "offline" };
type AskLoad = { response: WorkbenchAskResponse; source: "live" | "offline" };

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = window.sessionStorage.getItem("llmWikiApiToken");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}

async function loadSettings(): Promise<{ settings: SettingsSnapshot; source: "live" | "offline" }> {
  try {
    const response = await apiFetch(`/api/settings?workspace_id=${encodeURIComponent(workspaceId)}`, { headers: { accept: "application/json" } });
    if (response.ok) return { settings: (await response.json()) as SettingsSnapshot, source: "live" };
  } catch {
    // The checked-in fixture keeps the browser usable without a running host.
  }
  return { settings: sampleSettings, source: "offline" };
}

const workspaceId = new URLSearchParams(window.location.search).get("workspace_id") || "rl-fixture";

async function loadLens(query: string, anchorId?: string, pinnedNodeIds: string[] = [], allowOffline = true): Promise<LensLoad> {
  const params = new URLSearchParams({ workspace_id: workspaceId, query });
  if (anchorId) params.set("explicit_anchor_id", anchorId);
  for (const nodeId of pinnedNodeIds) params.append("pinned_node_id", nodeId);
  const endpoint = `/api/lens?${params.toString()}`;
  try {
    const response = await apiFetch(endpoint, { headers: { accept: "application/json" } });
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
    const response = await apiFetch(endpoint, {
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
        const polled = await apiFetch(`/api/interactions?${params.toString()}`, { headers: { accept: "application/json" }, signal });
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<SettingsSnapshot>(sampleSettings);
  const [settingsSource, setSettingsSource] = useState<"live" | "offline">("offline");
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState("");
  const [apiToken, setApiToken] = useState(() => window.sessionStorage.getItem("llmWikiApiToken") ?? "");
  const [composeMode, setComposeMode] = useState("gpu");
  const [composeBackend, setComposeBackend] = useState("postgres");
  const [composeOtel, setComposeOtel] = useState(false);
  const [composeOauth, setComposeOauth] = useState(false);
  const [composeRevision, setComposeRevision] = useState("");
  const [composeMaxModelLen, setComposeMaxModelLen] = useState("8192");
  const [composeCropBudget, setComposeCropBudget] = useState("7680");
  const [composeAuthMode, setComposeAuthMode] = useState("disabled");
  const [parserProvider, setParserProvider] = useState("");
  const [parserModel, setParserModel] = useState("");
  const [parserEndpoint, setParserEndpoint] = useState("");
  const [maintenanceProvider, setMaintenanceProvider] = useState("");
  const [maintenanceModel, setMaintenanceModel] = useState("");
  const [maintenanceEndpoint, setMaintenanceEndpoint] = useState("");
  const [composeYaml, setComposeYaml] = useState("");
  const [modelCatalog, setModelCatalog] = useState<{ parser: string[]; maintenance: string[] }>({ parser: [], maintenance: [] });
  const sessionId = useRef(`browser-${Math.random().toString(36).slice(2)}`);
  const activeRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    void loadSettings().then((loaded) => {
      setSettings(loaded.settings);
      setSettingsSource(loaded.source);
      setParserProvider(String(loaded.settings.effective.parser?.provider ?? ""));
      setParserModel(String(loaded.settings.effective.parser?.model ?? ""));
      setParserEndpoint(String(loaded.settings.effective.parser?.base_url ?? ""));
      setMaintenanceProvider(String(loaded.settings.effective.maintenance?.provider ?? ""));
      setMaintenanceModel(String(loaded.settings.effective.maintenance?.model ?? ""));
      setMaintenanceEndpoint(String(loaded.settings.effective.maintenance?.base_url ?? ""));
      setComposeMaxModelLen(String(loaded.settings.effective.multimodal?.max_model_len ?? 8192));
      setComposeCropBudget(String(loaded.settings.effective.multimodal?.crop_token_budget ?? 7680));
    });
    void Promise.all(["parser", "maintenance"].map(async (role) => {
      try {
        const response = await apiFetch(`/api/models?role=${role}&workspace_id=${encodeURIComponent(workspaceId)}`);
        const payload = await response.json() as { models?: string[] };
        return [role, payload.models ?? []] as const;
      } catch { return [role, []] as const; }
    })).then((entries) => setModelCatalog(Object.fromEntries(entries) as { parser: string[]; maintenance: string[] }));
  }, []);

  async function saveDesiredSettings(changes: Record<string, unknown>) {
    setSettingsSaving(true);
    setSettingsMessage("");
    try {
      const response = await apiFetch("/api/settings/desired", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId, settings: changes }),
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({})) as { detail?: string; error?: string };
        throw new Error(failure.detail ?? failure.error ?? `Settings request failed (${response.status})`);
      }
      setSettings(await response.json() as SettingsSnapshot);
      setSettingsMessage("Desired settings saved. The effective process is unchanged until the reported apply step.");
    } catch (error) {
      setSettingsMessage(error instanceof Error ? error.message : "Settings request failed");
    } finally { setSettingsSaving(false); }
  }

  async function applySettings() {
    setSettingsSaving(true);
    try {
      const response = await apiFetch("/api/settings/apply", {
        method: "POST", headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId, confirmed: true }),
      });
      setSettings(await response.json() as SettingsSnapshot);
      setSettingsMessage("Apply recorded. Follow the restart or re-embedding instructions before treating it as active.");
    } catch { setSettingsMessage("Could not reach the settings service"); }
    finally { setSettingsSaving(false); }
  }

  async function previewCompose() {
    setSettingsMessage("");
    try {
      const response = await apiFetch("/api/compose/preview", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ workspace_id: workspaceId, auth_mode: composeAuthMode, backend: composeBackend, mode: composeMode, with_otel: composeOtel, with_oauth: composeOauth, model_revision: composeRevision, embedding_max_model_len: Number(composeMaxModelLen), embedding_crop_token_budget: Number(composeCropBudget) }),
      });
      const result = await response.json() as { valid?: boolean; errors?: string[]; yaml?: string | null; detail?: string; error?: string };
      if (!response.ok) {
        setComposeYaml("");
        setSettingsMessage(result.detail ?? result.error ?? `Compose preview failed (${response.status})`);
        return;
      }
      if (!result.valid) { setComposeYaml(""); setSettingsMessage((result.errors ?? ["Compose configuration is invalid"]).join(" ")); return; }
      setComposeYaml(result.yaml ?? "");
      setSettingsMessage("Compose preview generated. Save it to a new file, then run the CLI check before starting containers.");
    } catch { setSettingsMessage("Could not reach the Compose configuration service"); }
  }

  async function scanModels(role: "parser" | "maintenance") {
    const provider = role === "parser" ? parserProvider : maintenanceProvider;
    const endpoint = role === "parser" ? parserEndpoint : maintenanceEndpoint;
    try {
      const params = new URLSearchParams({ role, workspace_id: workspaceId, provider, base_url: endpoint });
      const response = await apiFetch(`/api/models?${params.toString()}`, { headers: { accept: "application/json" } });
      const payload = await response.json() as { models?: string[]; source?: string };
      setModelCatalog((current) => ({ ...current, [role]: payload.models ?? [] }));
      setSettingsMessage(payload.source === "unavailable" ? `Could not reach ${provider || "the provider"}; manual model entry remains available.` : `Loaded ${payload.models?.length ?? 0} ${role} model suggestions.`);
    } catch {
      setSettingsMessage("Model discovery unavailable; enter a model name manually.");
    }
  }

  async function checkComposePreview() {
    if (!composeYaml) return;
    try {
      const response = await apiFetch("/api/compose/check", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ yaml: composeYaml }),
      });
      const result = await response.json() as { valid: boolean; errors?: string[] };
      setSettingsMessage(result.valid ? "Compose preview passed the structural checks. Run docker compose config --quiet before startup." : (result.errors ?? ["Compose preview is invalid"]).join(" "));
    } catch { setSettingsMessage("Could not validate the Compose preview"); }
  }

  function downloadComposePreview() {
    if (!composeYaml) return;
    const url = URL.createObjectURL(new Blob([composeYaml], { type: "text/yaml" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "compose.generated.yml";
    link.click();
    URL.revokeObjectURL(url);
  }

  useEffect(() => {
    if (!graphRoot.current) return;
    const graph = new MultiDirectedGraph();
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
        <div className="status"><span className="dot" /> {lens.nodes.length} visible / {lens.completeness} / <span className={lensSource === "live" ? "source-live" : "source-offline"}>{lensSource === "live" ? "live graph" : "offline fixture"}</span><button className="settings-toggle" onClick={() => setSettingsOpen((value) => !value)} aria-expanded={settingsOpen}>Settings</button></div>
      </header>
      {settingsOpen && <section className="settings-panel" aria-label="Operating settings">
        <div className="settings-heading"><div><p className="eyebrow">OPERATING CONSOLE</p><h2>System settings</h2>{settingsMessage && <p className="settings-message" role="status">{settingsMessage}</p>}</div><button onClick={() => setSettingsOpen(false)}>Close</button></div>
        <div className="settings-grid">
          <article className="settings-card"><p className="eyebrow">OVERVIEW</p><h3>{String(settings.effective.backend)}{settingsSource === "offline" ? " (offline fixture)" : ""}</h3><p>Workspace <strong>{settings.effective.workspace_id}</strong></p><p>Auth mode <strong>{settings.effective.auth_mode}</strong></p><p className="settings-note">{settingsSource === "live" ? "Effective values are measured from this running process." : "This is an offline fixture, not a report of the Docker deployment."} Compose generation below is a separate target configuration.</p></article>
          <article className="settings-card"><p className="eyebrow">EMBEDDING PLANES</p><h3>Knowledge text</h3>{Object.entries(settings.effective.embeddings ?? {}).map(([space, value]) => { const item = value as { backend?: string; profile?: Record<string, unknown>; profile_locked?: boolean }; const profile = item.profile ?? {}; return <div className="profile-row" key={space}><strong>{space}</strong><span>{String(profile.provider ?? "unknown")} / {String(profile.model ?? "unknown")}</span><small>{String(profile.dimension ?? "?")} dimensions / {item.backend ?? "backend"}{item.profile_locked ? " / profile locked" : ""}</small></div>; })}<p className="settings-note">The generated Compose default is PostgreSQL/pgvector. Chroma is shown only for the offline fixture or when explicitly selected.</p></article>
          <article className="settings-card"><p className="eyebrow">MULTIMODAL</p><h3>Docker Qwen3-VL</h3><p className={settings.effective.multimodal?.enabled ? "state-up" : "state-down"}>{settings.effective.multimodal?.enabled ? "Route available" : "Route disabled"}</p><p>{String(settings.effective.multimodal?.model ?? "Qwen3-VL")}, {String(settings.effective.multimodal?.dimension ?? 1024)}D</p><p>Context {String(settings.effective.multimodal?.max_model_len ?? 8192)} tokens; crop budget {String(settings.effective.multimodal?.crop_token_budget ?? 7680)} tokens.</p><button disabled={settingsSaving} onClick={() => void saveDesiredSettings({ multimodal_enabled: !settings.effective.multimodal?.enabled })}>{settings.effective.multimodal?.enabled ? "Disable route" : "Enable route"}</button><p className="settings-note">This toggles retrieval use; it does not change the stored vector profile. Context and crop changes require restart and re-embedding.</p></article>
          <article className="settings-card"><p className="eyebrow">MODELS</p><h3>Thinking workers</h3><label>Parser provider<input value={parserProvider} onChange={(event) => setParserProvider(event.target.value)} onBlur={(event) => void saveDesiredSettings({ parser_provider: event.target.value })} list="providers" placeholder="ollama, openai, router" /></label><label>Parser endpoint<input value={parserEndpoint} onChange={(event) => setParserEndpoint(event.target.value)} onBlur={(event) => void saveDesiredSettings({ parser_base_url: event.target.value })} placeholder="http://localhost:11434" /></label><label>Parser model<input list="parser-models" value={parserModel} onChange={(event) => setParserModel(event.target.value)} onBlur={(event) => void saveDesiredSettings({ parser_model: event.target.value })} /><button onClick={() => void scanModels("parser")}>Scan parser models</button><datalist id="parser-models">{modelCatalog.parser.map((model) => <option value={model} key={model} />)}</datalist></label><label>Maintenance provider<input value={maintenanceProvider} onChange={(event) => setMaintenanceProvider(event.target.value)} onBlur={(event) => void saveDesiredSettings({ maintenance_provider: event.target.value })} list="providers" placeholder="ollama, openai, router" /></label><label>Maintenance endpoint<input value={maintenanceEndpoint} onChange={(event) => setMaintenanceEndpoint(event.target.value)} onBlur={(event) => void saveDesiredSettings({ maintenance_base_url: event.target.value })} placeholder="http://localhost:11434" /></label><label>Maintenance model<input list="maintenance-models" value={maintenanceModel} onChange={(event) => setMaintenanceModel(event.target.value)} onBlur={(event) => void saveDesiredSettings({ maintenance_model: event.target.value })} /><button onClick={() => void scanModels("maintenance")}>Scan maintenance models</button><datalist id="maintenance-models">{modelCatalog.maintenance.map((model) => <option value={model} key={model} />)}</datalist></label><datalist id="providers"><option value="ollama" /><option value="openai" /><option value="azure" /><option value="router" /><option value="fake" /></datalist><p className="settings-note">Provider and endpoint discovery is advisory. Ollama uses <code>/api/tags</code>; OpenAI-compatible and router endpoints use <code>/v1/models</code>. If discovery fails, type any supported provider/model manually. Changes require a graceful restart. Codex subscription cockpit is separate from structured parser and maintenance workers.</p></article>
        <article className="settings-card"><p className="eyebrow">OBSERVABILITY</p><h3>OpenTelemetry sink</h3><p className={settings.effective.otel?.enabled ? "state-up" : "state-down"}>{settings.effective.otel?.enabled ? "Tracing enabled" : "Tracing disabled"}</p><p>{String(settings.effective.otel?.service_name ?? "kogwistar-llm-wiki")}</p><p className="settings-note">{settings.effective.otel?.endpoint ? `Collector: ${String(settings.effective.otel.endpoint)}` : "No OTLP collector endpoint configured"}</p><button disabled={settingsSaving || settings.effective.otel?.packages_available === false} onClick={() => void saveDesiredSettings({ otel_enabled: !settings.effective.otel?.enabled })}>{settings.effective.otel?.enabled ? "Disable tracing" : "Enable tracing"}</button><p className="settings-note">Grafana/collector availability is checked separately; this toggle controls the current process sink.</p></article>
        <article className="settings-card"><p className="eyebrow">IDENTITY</p><h3>OAuth / OIDC</h3><p className={settings.effective.auth_mode === "disabled" ? "state-down" : "state-up"}>{settings.effective.auth_mode === "disabled" ? "Personal mode" : `Managed: ${String(settings.effective.auth_mode)}`}</p><label>Authentication mode<select aria-label="Authentication mode" defaultValue={String(settings.desired.auth_mode ?? settings.effective.auth_mode ?? "disabled")} onChange={(event) => void saveDesiredSettings({ auth_mode: event.target.value })}><option value="disabled">Personal (no identity)</option><option value="static_token">Static token</option><option value="kogwistar_jwt">OAuth/OIDC JWT</option></select></label><label>Session bearer token<input aria-label="Session bearer token" type="password" value={apiToken} onChange={(event) => { setApiToken(event.target.value); if (event.target.value) window.sessionStorage.setItem("llmWikiApiToken", event.target.value); else window.sessionStorage.removeItem("llmWikiApiToken"); }} placeholder="Only needed for authenticated APIs" /></label><p className="settings-note">The token stays in this browser tab and is sent only to this same-origin API. It is never persisted by LLM-Wiki or included in settings responses.</p><p className="settings-note">This stages the mode in app settings. Update Compose JWT/OIDC variables, restart gracefully, and verify workspace ACLs before exposure.</p></article>
          <article className="settings-card compose-card"><p className="eyebrow">DOCKER SETUP</p><h3>Compose helper</h3><p className="settings-note">Generate a safe starting bundle. Secrets remain environment placeholders.</p><label>Graph backend<select aria-label="Graph backend" value={composeBackend} onChange={(event) => setComposeBackend(event.target.value)}><option value="postgres">PostgreSQL / pgvector (recommended)</option><option value="chroma" disabled>Embedded Chroma (single-process only)</option></select></label><p className="settings-note">This two-process REST/MCP bundle uses PostgreSQL. Use the single-process demo command for embedded Chroma.</p><label>Embedding runtime<select aria-label="Embedding runtime" value={composeMode} onChange={(event) => setComposeMode(event.target.value)}><option value="gpu">GPU / Qwen3-VL</option><option value="cpu">CPU / Qwen3-VL</option><option value="text-only">Text only</option></select></label><label>Embedding context limit<input aria-label="Embedding context limit" type="number" min="1" value={composeMaxModelLen} onChange={(event) => setComposeMaxModelLen(event.target.value)} /></label><label>Embedding crop budget<input aria-label="Embedding crop budget" type="number" min="1" value={composeCropBudget} onChange={(event) => setComposeCropBudget(event.target.value)} /></label><p className="settings-note">The crop budget must not exceed the context limit. Token-aware providers use it before inference; other providers retain character fallback.</p><label>Authentication<select aria-label="Compose authentication" value={composeAuthMode} onChange={(event) => setComposeAuthMode(event.target.value)}><option value="disabled">Personal local mode</option><option value="static_token">Static token</option><option value="kogwistar_jwt">OAuth/OIDC JWT</option></select></label><label className="check-label"><input type="checkbox" checked={composeOtel} onChange={(event) => setComposeOtel(event.target.checked)} /> Include Grafana OTel</label><label className="check-label"><input type="checkbox" checked={composeOauth} onChange={(event) => setComposeOauth(event.target.checked)} /> Include optional OAuth test provider</label><label>Model revision<input aria-label="Model revision" value={composeRevision} onChange={(event) => setComposeRevision(event.target.value)} placeholder="required for Qwen3-VL" /></label><p className="settings-note">OAuth is an optional Compose profile and does not configure JWT verification by itself.</p><button disabled={settingsSaving} onClick={() => void previewCompose()}>Preview Compose</button>{composeYaml && <><div className="compose-actions"><button onClick={checkComposePreview}>Check preview</button><button onClick={downloadComposePreview}>Download YAML</button></div><textarea aria-label="Generated Compose YAML" readOnly value={composeYaml} rows={10} /></>}</article>
        </div>
        {(settings.restart_required || settings.reembedding_required || settings.warnings.length > 0) && <div className="settings-warning" role="status"><strong>Apply impact</strong>{settings.warnings.map((warning) => <p key={warning}>{warning}</p>)}{settings.restart_required && <p>Restart the LLM-Wiki service gracefully after reviewing the desired configuration.</p>}{settings.reembedding_required && <p>Create an isolated projection and re-embed before cutover.</p>}<button disabled={settingsSaving} onClick={() => void applySettings()}>Acknowledge and stage apply</button></div>}
      </section>}
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
