export type LensNode = {
  id: string;
  graph_space: string;
  namespace: string;
  label: string;
  node_type: string;
  entity_revision: string | number | null;
  metadata: Record<string, unknown>;
  grounding: Array<Record<string, unknown>>;
  payload: Record<string, unknown>;
};

export type LensEdge = {
  id: string;
  graph_space: string;
  namespace: string;
  source_ids: string[];
  target_ids: string[];
  relation: string;
  entity_revision: string | number | null;
  metadata: Record<string, unknown>;
  grounding: Array<Record<string, unknown>>;
  payload: Record<string, unknown>;
};

export type LensParticipation = { edge_id: string; node_id: string; role: string };
export type SelectionExplanation = {
  node_id: string;
  reason: string;
  score: number;
  matched_terms: string[];
  distance: number | null;
};

export type LensSnapshot = {
  lens_id: string;
  workspace_id: string;
  source_watermark: string | number | null;
  projected_at_ms: number;
  completeness: string;
  nodes: LensNode[];
  edges: LensEdge[];
  hyperedges: LensEdge[];
  participations: LensParticipation[];
  anchor_explanations: SelectionExplanation[];
  selection_explanations: SelectionExplanation[];
  omitted_summary: Record<string, number>;
  query_timing_ms: number;
};

export type WorkbenchAnswer = {
  text: string;
  outcome: string;
  lens_id: string;
  source_watermark: string | number | null;
  cited_entity_ids: string[];
  insufficiency_reason: string | null;
  proposal?: Record<string, unknown> | null;
};

export type WorkbenchAskResponse = {
  interaction_id?: string;
  mode: "deterministic" | "codex";
  agent_status: "active" | "cockpit_active" | "not_configured" | "not_requested";
  answer: WorkbenchAnswer;
  snapshot: LensSnapshot;
  history: { id: string; session_id: string; lens_id: string; outcome: string; created_at_ms: number };
  proposal_request?: Record<string, unknown>;
};

export type WorkbenchInteraction = {
  interaction_id: string;
  workspace_id: string;
  session_id: string;
  status: "pending" | "completed" | "failed";
  submitted_at_ms: number;
  completed_at_ms: number | null;
  response: WorkbenchAskResponse | null;
  error: string | null;
};

export type SettingsSnapshot = {
  version: number;
  effective: Record<string, any>;
  desired: Record<string, any>;
  components: Record<string, { state: string; toggleable?: boolean; description?: string }>;
  restart_required: boolean;
  reembedding_required: boolean;
  warnings: string[];
};

export const sampleSettings: SettingsSnapshot = {
  version: 1,
  effective: {
    workspace_id: "rl-fixture",
    backend: "Offline fixture",
    data_dir: "local fixture",
    parser: { provider: "ollama", model: "gemma4:e2b" },
    maintenance: { provider: "ollama", model: "gemma4:e2b" },
    embeddings: {
      kg: { backend: "Offline fixture", profile: { provider: "fake", model: "kogwistar-llm-wiki-embedding-v1", dimension: 2, similarity_metric: "cosine" }, profile_locked: false },
    },
    multimodal: { enabled: false, configured: false, model: "Qwen/Qwen3-VL-Embedding-2B", dimension: 1024, service_url: null },
    auth_mode: "disabled",
    otel: { enabled: false, configured: false, endpoint: null, service_name: "kogwistar-llm-wiki", packages_available: true },
  },
  desired: {},
  components: {
    knowledge_text_embedding: { state: "up", toggleable: false, description: "Local text embedding used by the configured graph backend." },
    multimodal_embedding: { state: "disabled", toggleable: true, description: "Qwen3-VL remote projection route." },
    parser: { state: "up", toggleable: false },
    maintenance: { state: "up", toggleable: false },
    otel_sink: { state: "disabled", toggleable: true, description: "Optional OpenTelemetry trace sink for the configured collector." },
  },
  restart_required: false,
  reembedding_required: false,
  warnings: [],
};

export const sampleLens: LensSnapshot = {
  lens_id: "lens:offline-learning",
  workspace_id: "rl-fixture",
  source_watermark: 12,
  projected_at_ms: Date.now(),
  completeness: "bounded",
  nodes: [
    { id: "verifier", graph_space: "curated_kg", namespace: "fixture", label: "Verifier", node_type: "method", entity_revision: null, metadata: { status: "grounded" }, grounding: [{ doc_id: "src:rl:verifiers", excerpt: "A verifier checks an outcome." }], payload: {} },
    { id: "outcome", graph_space: "curated_kg", namespace: "fixture", label: "Outcome reward", node_type: "method", entity_revision: null, metadata: { status: "grounded" }, grounding: [{ doc_id: "src:rl:verifiers", excerpt: "outcome reward" }], payload: {} },
    { id: "hacking", graph_space: "curated_kg", namespace: "fixture", label: "Reward hacking", node_type: "risk", entity_revision: null, metadata: { status: "grounded" }, grounding: [{ doc_id: "src:rl:limits", excerpt: "reward hacking" }], payload: {} },
  ],
  edges: [
    { id: "supports", graph_space: "curated_kg", namespace: "fixture", source_ids: ["verifier"], target_ids: ["outcome"], relation: "supports", entity_revision: null, metadata: {}, grounding: [{ doc_id: "src:rl:verifiers", excerpt: "verifier" }], payload: {} },
    { id: "warns", graph_space: "curated_kg", namespace: "fixture", source_ids: ["hacking"], target_ids: ["outcome"], relation: "weakens", entity_revision: null, metadata: {}, grounding: [{ doc_id: "src:rl:limits", excerpt: "reward hacking" }], payload: {} },
  ],
  hyperedges: [{ id: "group-relative", graph_space: "curated_kg", namespace: "fixture", source_ids: ["verifier"], target_ids: ["outcome", "hacking"], relation: "compares candidates", entity_revision: null, metadata: {}, grounding: [{ doc_id: "src:rl:verifiers", excerpt: "candidate comparison" }], payload: {} }],
  participations: [
    { edge_id: "group-relative", node_id: "verifier", role: "source" },
    { edge_id: "group-relative", node_id: "outcome", role: "target" },
    { edge_id: "group-relative", node_id: "hacking", role: "target" },
  ],
  anchor_explanations: [],
  selection_explanations: [
    { node_id: "verifier", reason: "query_match", score: 1, matched_terms: ["verifier"], distance: 0 },
    { node_id: "outcome", reason: "traversed_neighbor", score: 0, matched_terms: [], distance: 1 },
    { node_id: "hacking", reason: "traversed_neighbor", score: 0, matched_terms: [], distance: 1 },
  ],
  omitted_summary: { candidate_nodes: 0, candidate_edges: 0, candidate_hyperedges: 0 },
  query_timing_ms: 2,
};
